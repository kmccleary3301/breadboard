#!/usr/bin/env python3
"""
Replay an OpenCode session’s tool calls against the KyleCode harness.

Usage:
    python scripts/replay_opencode_session.py \
        --config agent_configs/opencode_grok4fast_c_fs_guardrails.yaml \
        --session misc/opencode_tests/protofs_test_run/opencode_session_dump.json \
        --workspace /tmp/opencode_replay_ws
"""
from __future__ import annotations

import argparse
import asyncio
import ast
import copy
import hashlib
import json
import os
import re
import shutil
import sys
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, Iterable, List, Optional, Tuple

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from kylecode.sandbox_v2 import DevSandboxV2  # type: ignore
from agentic_coder_prototype.compilation.v2_loader import load_agent_config, is_v2_config  # type: ignore
from agentic_coder_prototype.compilation.tool_yaml_loader import load_yaml_tools  # type: ignore
from agentic_coder_prototype.compilation.system_prompt_compiler import get_compiler  # type: ignore
from agentic_coder_prototype.core.core import ToolDefinition, ToolParameter  # type: ignore
from agentic_coder_prototype.provider_runtime import (  # type: ignore
    ProviderResult,
    ProviderMessage,
    ProviderToolCall,
    ProviderRuntime,
    provider_registry,
)
from agentic_coder_prototype.dialects.aider_diff import AiderDiffDialect  # type: ignore
from agentic_coder_prototype.dialects.bash_block import BashBlockDialect  # type: ignore
from agentic_coder_prototype.dialects.opencode_patch import OpenCodePatchDialect  # type: ignore
from agentic_coder_prototype.dialects.pythonic02 import Pythonic02Dialect  # type: ignore
from agentic_coder_prototype.dialects.pythonic_inline import PythonicInlineDialect  # type: ignore
from agentic_coder_prototype.dialects.unified_diff import UnifiedDiffDialect  # type: ignore
from agentic_coder_prototype.dialects.yaml_command import YAMLCommandDialect  # type: ignore
from agentic_coder_prototype.execution.dialect_manager import DialectManager  # type: ignore
from agentic_coder_prototype.execution.enhanced_executor import EnhancedToolExecutor  # type: ignore
from agentic_coder_prototype.todo.store import TodoStore  # type: ignore
from agentic_coder_prototype.todo.manager import TodoManager  # type: ignore
from agentic_coder_prototype.agent_llm_openai import OpenAIConductor  # type: ignore
from agentic_coder_prototype.state.session_state import SessionState  # type: ignore


@dataclass
class ToolCall:
    """Represents a single OpenCode tool invocation."""

    tool: str
    input: Dict[str, Any]
    status: Optional[str] = None
    output: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None

    def expected_exit(self) -> Optional[int]:
        meta = self.metadata or {}
        exit_value = meta.get("exit") if isinstance(meta, dict) else None
        return exit_value if isinstance(exit_value, int) else None


@dataclass
class AssistantTurn:
    """Grouped assistant response from OpenCode trace."""

    message_id: str
    text_parts: List[str] = field(default_factory=list)
    tool_calls: List[ToolCall] = field(default_factory=list)
    raw: Dict[str, Any] = field(default_factory=dict)


def sha256_text(value: Optional[str]) -> str:
    text = value or ""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_json(payload: Any) -> str:
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def relative_to_root(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT_DIR))
    except ValueError:
        return str(path.resolve())


def resolve_active_mode(config: Dict[str, Any]) -> Optional[str]:
    try:
        seq = (config.get("loop", {}) or {}).get("sequence") or []
        features = config.get("features", {}) or {}
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
        modes = config.get("modes", []) or []
        if modes and isinstance(modes, list):
            name = modes[0].get("name")
            if name:
                return str(name)
    except Exception:
        pass
    return None


def get_mode_config(config: Dict[str, Any], mode_name: Optional[str]) -> Dict[str, Any]:
    if not mode_name:
        return {}
    try:
        for mode_cfg in config.get("modes", []) or []:
            if mode_cfg.get("name") == mode_name:
                return mode_cfg
    except Exception:
        pass
    return {}


def prompts_config_with_todos(config: Dict[str, Any]) -> Dict[str, Any]:
    features_cfg = (config.get("features") or {}).get("todos") or {}
    if not features_cfg.get("enabled"):
        return copy.deepcopy(config)
    cfg = copy.deepcopy(config)
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


def load_tool_definitions(config: Dict[str, Any], config_path: Path) -> List[ToolDefinition]:
    tools_cfg = (config.get("tools", {}) or {})
    registry_cfg = (tools_cfg.get("registry", {}) or {})
    include_list = [str(item) for item in (registry_cfg.get("include") or [])]
    legacy_enabled = (tools_cfg.get("enabled", {}) or {})
    overlays = tools_cfg.get("overlays") or []
    aliases = tools_cfg.get("aliases") or {}

    def _is_included(name: str) -> bool:
        if include_list:
            return name in include_list
        if legacy_enabled:
            return bool(legacy_enabled.get(name, False))
        return True

    search_paths: List[Path] = []
    for entry in registry_cfg.get("paths") or []:
        path_obj = Path(entry)
        if not path_obj.is_absolute():
            candidate = (config_path.parent / path_obj).resolve()
            if candidate.exists():
                search_paths.append(candidate)
                continue
            path_obj = (ROOT_DIR / path_obj).resolve()
        search_paths.append(path_obj)

    defs_dir = tools_cfg.get("defs_dir")
    if not search_paths and defs_dir:
        path_obj = Path(defs_dir)
        if not path_obj.is_absolute():
            candidate = (config_path.parent / path_obj).resolve()
            if candidate.exists():
                search_paths.append(candidate)
            else:
                search_paths.append((ROOT_DIR / path_obj).resolve())
        else:
            search_paths.append(path_obj)

    if not search_paths:
        search_paths.append((ROOT_DIR / "implementations/tools/defs").resolve())

    collected: "OrderedDict[str, ToolDefinition]" = OrderedDict()
    for dir_path in search_paths:
        if not dir_path.exists():
            continue
        try:
            loaded = load_yaml_tools(str(dir_path), overlays=overlays, aliases=aliases)
        except Exception:
            continue
        for tool in loaded.tools:
            name = getattr(tool, "name", None)
            if not name or not _is_included(name) or name in collected:
                continue
            params = []
            for param in getattr(tool, "parameters", []) or []:
                params.append(
                    ToolParameter(
                        name=param.name,
                        type=param.type,
                        description=param.description,
                        default=param.default,
                    )
                )
            collected[name] = ToolDefinition(
                type_id=getattr(tool, "type_id", "python"),
                name=name,
                description=getattr(tool, "description", ""),
                parameters=params,
                blocking=bool(getattr(tool, "blocking", False)),
            )
    return list(collected.values())


def filter_tools_by_mode(tool_defs: List[ToolDefinition], mode_cfg: Dict[str, Any]) -> List[ToolDefinition]:
    try:
        enabled = mode_cfg.get("tools_enabled")
        disabled = mode_cfg.get("tools_disabled") or []
        if not enabled and not disabled:
            return tool_defs
        enabled_set = set(enabled or [])
        disabled_set = set(disabled)
        out: List[ToolDefinition] = []
        for tool in tool_defs:
            name = tool.name
            if name in disabled_set:
                continue
            if enabled and "*" not in enabled_set and name not in enabled_set:
                continue
            out.append(tool)
        return out or tool_defs
    except Exception:
        return tool_defs


def create_dialect_mapping() -> Dict[str, Any]:
    return {
        "pythonic02": Pythonic02Dialect(),
        "pythonic_inline": PythonicInlineDialect(),
        "bash_block": BashBlockDialect(),
        "aider_diff": AiderDiffDialect(),
        "unified_diff": UnifiedDiffDialect(),
        "opencode_patch": OpenCodePatchDialect(),
        "yaml_command": YAMLCommandDialect(),
    }


def apply_selection_legacy(
    current: List[str],
    model_id: str,
    tool_defs: List[ToolDefinition],
    selection_cfg: Dict[str, Any],
) -> List[str]:
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

    present_types = {definition.type_id for definition in (tool_defs or []) if getattr(definition, "type_id", None)}
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
    ordered_pref = [dialect for dialect in preferred if (dialect not in seen and not seen.add(dialect))]
    remaining = [dialect for dialect in current if dialect not in ordered_pref]
    return ordered_pref + remaining


def apply_preference_order(
    base_order: List[str],
    model_id: str,
    tool_defs: List[ToolDefinition],
    preference_cfg: Dict[str, Any],
) -> Tuple[List[str], Optional[bool]]:
    import fnmatch as _fnmatch

    available = list(base_order)
    available_set = set(available)
    preferred: List[str] = []
    seen = set()
    native_hint: Optional[bool] = None

    def normalize_entry(entry: Any) -> Tuple[List[str], Optional[bool]]:
        native_override: Optional[bool] = None
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

    global_entries = preference_cfg.get("global")
    if global_entries:
        extend(global_entries)

    by_model_pref = preference_cfg.get("by_model", {})
    for pattern, pref in (by_model_pref or {}).items():
        try:
            if _fnmatch.fnmatch(model_id or "", pattern):
                extend(pref)
        except Exception:
            continue

    ordered = []
    seen_local = set()
    for name in preferred:
        if name in available_set and name not in seen_local:
            ordered.append(name)
            seen_local.add(name)
    remaining = [name for name in available if name not in seen_local]
    return ordered + remaining, native_hint


def apply_v2_dialect_selection(
    config: Dict[str, Any],
    current: List[str],
    model_id: str,
    tool_defs: List[ToolDefinition],
) -> List[str]:
    tools_cfg = (config.get("tools", {}) or {})
    dialects_cfg = (tools_cfg.get("dialects", {}) or {})
    selection_cfg = (dialects_cfg.get("selection", {}) or {})
    base_order = apply_selection_legacy(current, model_id, tool_defs, selection_cfg)

    preference_cfg = (dialects_cfg.get("preference", {}) or {})
    if preference_cfg:
        ordered, _ = apply_preference_order(base_order, model_id, tool_defs, preference_cfg)
        return ordered
    return base_order


def dump_tool_defs(tool_defs: List[ToolDefinition]) -> List[Dict[str, Any]]:
    result: List[Dict[str, Any]] = []
    for tool in tool_defs:
        result.append(
            {
                "type_id": tool.type_id,
                "name": tool.name,
                "description": tool.description,
                "parameters": [
                    {
                        "name": param.name,
                        "type": param.type,
                        "description": param.description,
                        "default": param.default,
                    }
                    for param in tool.parameters
                ],
            }
        )
    return result


def compute_prompt_hashes(config_path: Path, config: Dict[str, Any]) -> Dict[str, str]:
    cfg_copy = copy.deepcopy(config)
    mode_name = resolve_active_mode(cfg_copy)
    tool_defs = load_tool_definitions(cfg_copy, config_path)
    mode_cfg = get_mode_config(cfg_copy, mode_name)
    prompt_tool_defs = filter_tools_by_mode(tool_defs, mode_cfg)

    dialect_mapping = create_dialect_mapping()
    requested_dialects = list(dialect_mapping.keys())
    manager = DialectManager(cfg_copy)
    model_id = ((cfg_copy.get("providers") or {}).get("default_model") or "")
    active_dialect_names = manager.get_dialects_for_model(model_id, requested_dialects)
    if is_v2_config(cfg_copy):
        active_dialect_names = apply_v2_dialect_selection(cfg_copy, active_dialect_names, model_id, prompt_tool_defs)

    prompts_cfg = prompts_config_with_todos(cfg_copy)
    compiler = get_compiler()
    compiled = compiler.compile_v2_prompts(prompts_cfg, mode_name, prompt_tool_defs, active_dialect_names)
    per_turn_availability = compiler.format_per_turn_availability(
        [tool.name for tool in prompt_tool_defs],
        active_dialect_names,
    )

    base_pack = ((prompts_cfg.get("prompts") or {}).get("packs") or {}).get("base", {})
    todo_prompts: Dict[str, str] = {}
    for key in ("todo_plan", "todo_build"):
        path_str = base_pack.get(key)
        if not path_str:
            continue
        path_obj = Path(path_str)
        if not path_obj.is_absolute():
            candidate = (config_path.parent / path_obj).resolve()
            if candidate.exists():
                path_obj = candidate
            else:
                path_obj = (ROOT_DIR / path_obj).resolve()
        if path_obj.exists():
            todo_prompts[key] = path_obj.read_text(encoding="utf-8")

    tool_schema = dump_tool_defs(prompt_tool_defs)

    hashes = {
        "system_prompt_sha256": sha256_text(compiled.get("system")),
        "per_turn_prompt_sha256": sha256_text(compiled.get("per_turn")),
        "per_turn_availability_sha256": sha256_text(per_turn_availability),
        "tool_schema_sha256": sha256_json(tool_schema),
        "active_dialects_sha256": sha256_json(active_dialect_names),
    }
    for key, text in todo_prompts.items():
        hashes[f"{key}_sha256"] = sha256_text(text)
    return hashes


def validate_prompt_hashes(config_path: Path, config: Dict[str, Any], manifest_path: Optional[Path]) -> None:
    actual = compute_prompt_hashes(config_path, config)
    if not manifest_path:
        return
    if not manifest_path.exists():
        print(f"[hash-check] Manifest not found at {manifest_path}. Computed hashes:\n{json.dumps(actual, indent=2)}")
        return
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    key = str(config_path.resolve().relative_to(ROOT_DIR))
    expected = manifest.get(key)
    if not expected:
        print(f"[hash-check] No entry for {key} in {manifest_path}. Computed hashes:\n{json.dumps(actual, indent=2)}")
        return
    mismatches = []
    for name, expected_value in expected.items():
        actual_value = actual.get(name)
        if actual_value != expected_value:
            mismatches.append((name, expected_value, actual_value))
    if mismatches:
        lines = [
            f"{name}: expected {exp}, actual {act}"
            for name, exp, act in mismatches
        ]
        raise RuntimeError(
            "Prompt/tool hash validation failed:\n" + "\n".join(lines) +
            "\nRecompute hashes and update the manifest if this change is intentional."
        )


def prepare_replay_config(config: Dict[str, Any], workspace_path: Path) -> Dict[str, Any]:
    cfg = copy.deepcopy(config)
    providers = cfg.setdefault("providers", {})
    providers["default_model"] = "mock/replay"
    providers["models"] = [
        {"id": "mock/replay", "adapter": "mock", "params": {"temperature": 0.0}}
    ]

    provider_tools = cfg.setdefault("provider_tools", {})
    provider_tools["use_native"] = False
    provider_tools["suppress_prompts"] = False

    # Disable guardrails that rely on live provider signals to avoid early termination
    loop_cfg = cfg.setdefault("loop", {})
    guardrails_cfg = loop_cfg.setdefault("guardrails", {})
    zero_tool_cfg = guardrails_cfg.setdefault("zero_tool_watchdog", {})
    zero_tool_cfg["warn_after_turns"] = 0
    zero_tool_cfg["abort_after_turns"] = 0

    tools_cfg = cfg.setdefault("tools", {})
    aliases_cfg = tools_cfg.setdefault("aliases", {})
    aliases_cfg.setdefault("bash", "run_shell")

    workspace_cfg = cfg.setdefault("workspace", {})
    workspace_cfg["root"] = str(workspace_path)
    mirror_cfg = workspace_cfg.setdefault("mirror", {})
    mirror_cfg["enabled"] = False

    return cfg

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Replay OpenCode tool calls inside KyleCode harness.")
    parser.add_argument("--config", required=True, help="Path to KyleCode agent config (YAML).")
    parser.add_argument("--session", required=True, help="Path to OpenCode session dump (JSON).")
    parser.add_argument("--workspace", default="./opencode_replay_ws", help="Workspace directory to use.")
    parser.add_argument("--limit", type=int, default=None, help="Maximum tool calls to replay.")
    parser.add_argument("--todo-expected", default=None, help="Optional path to expected todo events (JSONL).")
    parser.add_argument(
        "--hash-manifest",
        default=str(ROOT_DIR / "misc/opencode_tests/opencode_prompt_hashes.json"),
        help="Path to prompt/tool hash manifest JSON. Set to empty string to disable.",
    )
    parser.add_argument(
        "--golden-workspace",
        default="",
        help="Optional path to a golden workspace for file-by-file comparison.",
    )
    return parser.parse_args()


def load_opencode_tool_calls(session_path: Path) -> List[ToolCall]:
    with session_path.open("r", encoding="utf-8") as handle:
        entries = json.load(handle)

    tool_calls: List[ToolCall] = []
    for entry in entries:
        if entry.get("role") != "assistant":
            continue
        for part in entry.get("parts", []):
            if part.get("type") != "tool":
                continue
            state = part.get("meta", {}).get("state") or {}
            input_payload = state.get("input") or {}
            tool_name = part.get("tool")
            if not tool_name:
                # Fall back to inferred mapping
                if "command" in input_payload:
                    tool_name = "bash"
                elif "filePath" in input_payload:
                    tool_name = "write"
            if not tool_name:
                continue
            tool_calls.append(
                ToolCall(
                    tool=tool_name,
                    input=input_payload,
                    status=state.get("status"),
                    output=state.get("output"),
                    metadata=state.get("metadata"),
                )
            )
    return tool_calls


def load_opencode_turns(session_path: Path) -> List[AssistantTurn]:
    with session_path.open("r", encoding="utf-8") as handle:
        entries = json.load(handle)

    turns: List[AssistantTurn] = []
    for entry in entries:
        if entry.get("role") != "assistant":
            continue
        parts = entry.get("parts", [])
        message_id = str(entry.get("message_id") or entry.get("id") or f"msg_{len(turns)}")
        text_buffer: List[str] = []
        tool_index = 0
        for part in parts:
            p_type = part.get("type")
            if p_type == "text":
                text = part.get("text")
                if isinstance(text, str) and text.strip():
                    text_buffer.append(text)
            elif p_type == "tool":
                state = part.get("meta", {}).get("state") or {}
                tool_calls_state = state.get("input") or {}
                new_turn = AssistantTurn(
                    message_id=f"{message_id}_{tool_index}",
                    text_parts=list(text_buffer),
                    tool_calls=[
                        ToolCall(
                            tool=part.get("tool") or "",
                            input=tool_calls_state,
                            status=state.get("status"),
                            output=state.get("output"),
                            metadata=state.get("metadata"),
                        )
                    ],
                    raw=entry,
                )
                turns.append(new_turn)
                tool_index += 1
                text_buffer = []
        if text_buffer:
            turns.append(
                AssistantTurn(
                    message_id=f"{message_id}_text",
                    text_parts=list(text_buffer),
                    tool_calls=[],
                    raw=entry,
                )
            )
    return turns


def derive_strip_prefix(paths: Iterable[str]) -> str:
    clean_paths = [p for p in paths if p]
    if not clean_paths:
        return ""
    try:
        return os.path.commonpath(clean_paths)
    except ValueError:
        # If paths are on different drives or invalid, fall back to directory of first path.
        first = clean_paths[0]
        return str(Path(first).parent)


def normalize_path(original_path: str, strip_prefix: str) -> str:
    if not original_path:
        return original_path
    path_obj = Path(original_path)
    try:
        relative = path_obj.relative_to(strip_prefix)
    except ValueError:
        relative = path_obj.name
    # Drop leading slash if present after relative conversion
    return str(relative).lstrip(os.sep)


TODO_PLACEHOLDER_RE = re.compile(r"\{\{\s*todo\[(\d+)\]\.(id|title)\s*\}\}", re.IGNORECASE)


def load_current_todos(workspace_path: Path) -> List[Dict[str, Any]]:
    data_path = workspace_path / TodoStore.FILENAME
    if not data_path.exists():
        return []
    try:
        payload = json.loads(data_path.read_text(encoding="utf-8"))
    except Exception:
        return []

    todos_by_id: Dict[str, Dict[str, Any]] = {}
    for item in payload.get("todos", []) or []:
        todo_id = item.get("id")
        if isinstance(todo_id, str):
            todos_by_id[todo_id] = item

    ordered: List[Dict[str, Any]] = []
    for todo_id in payload.get("order", []) or []:
        todo = todos_by_id.get(todo_id)
        if todo:
            ordered.append(todo)
    for todo in payload.get("todos", []) or []:
        if todo not in ordered:
            ordered.append(todo)
    return ordered


def resolve_todo_placeholders(value: Any, workspace_path: Path) -> Any:
    if isinstance(value, dict):
        return {k: resolve_todo_placeholders(v, workspace_path) for k, v in value.items()}
    if isinstance(value, list):
        return [resolve_todo_placeholders(item, workspace_path) for item in value]
    if isinstance(value, str):
        match = TODO_PLACEHOLDER_RE.fullmatch(value.strip())
        if not match:
            return value
        index = int(match.group(1))
        field = match.group(2).lower()
        todos = load_current_todos(workspace_path)
        if 0 <= index < len(todos):
            todo = todos[index]
            if field in todo:
                return todo[field]
        return value
    return value


def parse_iterable_status(status_value: Any) -> Tuple[str, Optional[int]]:
    """Convert the sandbox iterable status payload into stdout text and exit code."""
    if status_value is None:
        return "", None

    if isinstance(status_value, list):
        items = status_value
    elif isinstance(status_value, str):
        try:
            parsed = ast.literal_eval(status_value)
            items = parsed if isinstance(parsed, list) else [status_value]
        except (ValueError, SyntaxError):
            items = [status_value]
    else:
        items = [status_value]

    exit_code: Optional[int] = None
    lines: List[str] = []
    for item in items:
        if isinstance(item, str):
            if item.startswith(">>>>>"):
                continue
            lines.append(item)
        elif isinstance(item, dict) and "exit" in item and exit_code is None:
            exit_val = item.get("exit")
            if isinstance(exit_val, int):
                exit_code = exit_val
    stdout = "\n".join(lines).strip("\n")
    return stdout, exit_code


def summarize_result(tool_name: str, raw_result: Any) -> Dict[str, Any]:
    """Return a compact summary for comparison/reporting."""
    summary: Dict[str, Any] = {}
    if isinstance(raw_result, dict):
        if "error" in raw_result:
            summary["error"] = raw_result["error"]
        if tool_name in {"bash", "run_shell"}:
            stdout, exit_code = parse_iterable_status(raw_result.get("status"))
            if not stdout and isinstance(raw_result.get("stdout"), str):
                stdout = raw_result["stdout"].strip("\n")
            summary["stdout"] = stdout
            if exit_code is not None:
                summary["exit"] = exit_code
            else:
                for key in ("returncode", "exit_code"):
                    value = raw_result.get(key)
                    if isinstance(value, int):
                        summary["exit"] = value
                        break
        if tool_name in {"write", "opencode_write"} and "path" in raw_result:
            summary["path"] = raw_result["path"]
        if tool_name in {"patch", "apply_patch", "apply_unified_patch"} and "patch" in raw_result:
            summary["patch"] = raw_result["patch"]
    else:
        summary["value"] = raw_result
    return summary


def _equivalent_output(expected: str, actual: str, path_tokens: Iterable[str]) -> bool:
    if expected == actual:
        return True
    normalized_expected = expected
    normalized_actual = actual
    for token in path_tokens:
        if not token:
            continue
        normalized_expected = normalized_expected.replace(token, "[WORKSPACE]")
        normalized_actual = normalized_actual.replace(token, "[WORKSPACE]")
    return normalized_expected == normalized_actual


def compare_expected(
    call: ToolCall,
    summary: Dict[str, Any],
    *,
    path_normalizers: Optional[Iterable[str]] = None,
) -> List[str]:
    """Compare replay output against OpenCode expectations and capture mismatches."""
    mismatches: List[str] = []
    if call.status == "completed" and summary.get("error"):
        mismatches.append(f"expected status 'completed' but replay returned error: {summary['error']}")

    expected_exit = call.expected_exit()
    actual_exit = summary.get("exit")
    if expected_exit is not None and actual_exit is not None and expected_exit != actual_exit:
        mismatches.append(f"exit mismatch (expected {expected_exit}, got {actual_exit})")

    if call.tool in {"bash", "run_shell", "Bash"}:
        expected_output = call.output or ""
        actual_output = summary.get("stdout") or ""
        if isinstance(expected_output, dict):
            expected_output = json.dumps(expected_output, sort_keys=True)
        expected_output = str(expected_output).strip()
        actual_output = str(actual_output).strip()
        command = ""
        if isinstance(call.input, dict):
            command = str(call.input.get("command") or "")
        path_tokens = list(path_normalizers or [])
        if expected_output and actual_output and expected_output != actual_output:
            if not _equivalent_output(expected_output, actual_output, path_tokens):
                ls_or_pwd = bool(re.search(r"\b(ls|pwd)\b", command))
                if command.startswith("find ") or ls_or_pwd:
                    # directory listings differ only by workspace paths; treat as informational
                    pass
                else:
                    mismatches.append("stdout mismatch between OpenCode trace and replay")
        elif expected_output and not actual_output:
            mismatches.append("expected stdout present in OpenCode trace but replay captured none")

    return mismatches


async def replay_tool_call(
    executor: EnhancedToolExecutor,
    tool_name: str,
    payload: Dict[str, Any],
    *,
    workspace_path: Path,
    todo_manager: Optional[TodoManager],
    strip_prefix: str,
) -> Dict[str, Any]:
    """Replay a single tool call through the enhanced executor."""
    tool_name = tool_name or ""
    call: Dict[str, Any] = {"function": "", "arguments": {}}

    if tool_name in {"write", "opencode_write", "Write"}:
        file_path = payload.get("filePath") or payload.get("file_path") or payload.get("path")
        content = payload.get("content", "")
        normalized = normalize_path(str(file_path), strip_prefix)
        call["function"] = "write"
        call["arguments"] = {"path": normalized, "content": content}
    elif tool_name in {"patch", "apply_patch"}:
        patch_text = payload.get("patch") or payload.get("patchText")
        call["function"] = "patch" if tool_name == "patch" else "apply_unified_patch"
        call["arguments"] = {"patch": patch_text}
    elif tool_name in {"bash", "run_shell", "Bash"}:
        command = payload.get("command")
        call["function"] = "run_shell"
        call["arguments"] = {"cmd": command}
    elif tool_name in {"read", "read_file"}:
        file_path = payload.get("filePath") or payload.get("file_path") or payload.get("path")
        normalized = normalize_path(str(file_path), strip_prefix)
        call["function"] = "read" if tool_name == "read" else "read_file"
        arguments: Dict[str, Any] = {"path": normalized}
        if "offset" in payload and payload["offset"] is not None:
            arguments["offset"] = payload["offset"]
        if "limit" in payload and payload["limit"] is not None:
            arguments["limit"] = payload["limit"]
        call["arguments"] = arguments
    elif tool_name == "list":
        directory = payload.get("path") or "."
        normalized = normalize_path(str(directory), strip_prefix)
        arguments: Dict[str, Any] = {"path": normalized or "."}
        if "depth" in payload and payload["depth"] is not None:
            arguments["depth"] = payload["depth"]
        call["function"] = "list"
        call["arguments"] = arguments
    elif tool_name == "glob":
        pattern = payload.get("pattern") or payload.get("glob")
        search_path = payload.get("path")
        arguments: Dict[str, Any] = {}
        if pattern:
            arguments["pattern"] = pattern
        if search_path:
            arguments["path"] = normalize_path(str(search_path), strip_prefix)
        call["function"] = "glob"
        call["arguments"] = arguments
    elif tool_name == "grep":
        pattern = payload.get("pattern")
        arguments: Dict[str, Any] = {}
        if pattern:
            arguments["pattern"] = pattern
        if "path" in payload and payload["path"]:
            arguments["path"] = normalize_path(str(payload["path"]), strip_prefix)
        if "include" in payload and payload["include"]:
            arguments["include"] = payload["include"]
        call["function"] = "grep"
        call["arguments"] = arguments
    elif tool_name == "edit":
        file_path = payload.get("filePath") or payload.get("path")
        normalized = normalize_path(str(file_path), strip_prefix)
        old_text = payload.get("oldString", "")
        new_text = payload.get("newString", "")
        replace_all = bool(payload.get("replaceAll"))
        call["function"] = "edit"
        call["arguments"] = {
            "path": normalized,
            "old": old_text,
            "new": new_text,
            "count": 0 if replace_all else 1,
        }
    elif tool_name == "TodoWrite":
        call["function"] = "todo.write_board"
        call["arguments"] = payload if isinstance(payload, dict) else {}
    elif tool_name.startswith("todo."):
        call["function"] = tool_name
        call["arguments"] = payload if isinstance(payload, dict) else {}
    elif tool_name == "mark_task_complete":
        call["function"] = "mark_task_complete"
        call["arguments"] = {}
    else:
        # Unsupported tool for replay
        raise ValueError(f"Unsupported tool in replay: {tool_name}")

    call["arguments"] = resolve_todo_placeholders(call.get("arguments", {}), workspace_path)

    if call["function"].startswith("todo."):
        if todo_manager is None:
            raise ValueError("Todo manager not initialized for replay")
        function_name = call["function"].split(".", 1)[1]
        handler_map = {
            "create": todo_manager.handle_create,
            "update": todo_manager.handle_update,
            "complete": todo_manager.handle_complete,
            "cancel": todo_manager.handle_cancel,
            "reorder": todo_manager.handle_reorder,
            "attach": todo_manager.handle_attach,
            "note": todo_manager.handle_note,
            "list": todo_manager.handle_list,
        }
        handler = handler_map.get(function_name)
        if handler is None:
            raise ValueError(f"Unsupported todo tool: {function_name}")
        return handler(call["arguments"])

    if call["function"] == "mark_task_complete":
        return {"status": "completed"}

    return await executor.execute_tool_call(call)


def ensure_workspace(path: Path) -> None:
    if path.exists():
        for item in path.iterdir():
            if item.is_dir():
                shutil.rmtree(item)
            else:
                item.unlink()
    else:
        path.mkdir(parents=True, exist_ok=True)


def load_todo_journal(workspace_path: Path) -> List[Dict[str, Any]]:
    data_path = workspace_path / TodoStore.FILENAME
    if not data_path.exists():
        return []
    try:
        data = json.loads(data_path.read_text(encoding="utf-8"))
    except Exception:
        return []
    return list(data.get("journal") or [])


def load_expected_todos(path: Path) -> List[Dict[str, Any]]:
    events: List[Dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        events.append(json.loads(line))
    return events


def load_user_prompt(session_path: Path) -> str:
    with session_path.open("r", encoding="utf-8") as handle:
        entries = json.load(handle)
    for entry in entries:
        if entry.get("role") != "user":
            continue
        parts = entry.get("parts", [])
        texts = []
        for part in parts:
            if part.get("type") == "text":
                text = part.get("text")
                if isinstance(text, str):
                    texts.append(text)
    if texts:
        return "\n\n".join(texts).strip()
    raise ValueError("User prompt not found in session trace.")


def run_agentic_replay(
    config_path: Path,
    config: Dict[str, Any],
    turns: List[AssistantTurn],
    user_prompt: str,
    workspace_path: Path,
) -> Tuple[Dict[str, Any], ReplayContext]:
    ensure_workspace(workspace_path)
    os.environ.setdefault("MOCK_API_KEY", "replay-api-key")
    replay_config = prepare_replay_config(config, workspace_path)

    paths_for_prefix: List[Optional[str]] = []
    for turn in turns:
        for tool_call in turn.tool_calls:
            if tool_call.tool in {"write", "opencode_write"}:
                paths_for_prefix.append(tool_call.input.get("filePath") or tool_call.input.get("path"))
    strip_prefix = derive_strip_prefix(paths_for_prefix)

    context = ReplayContext(turns, workspace_path, strip_prefix)
    ReplayProviderRuntime.set_context(context)

    original_runtime_cls = provider_registry.get_runtime_class("mock_chat")
    provider_registry.register_runtime("mock_chat", ReplayProviderRuntime)

    try:
        conductor_cls = OpenAIConductor.__ray_metadata__.modified_class
        conductor = conductor_cls(
            workspace=str(workspace_path),
            config=replay_config,
            local_mode=True,
        )

        model_id = replay_config.get("providers", {}).get("default_model", "mock/replay")
        tool_prompt_mode = (replay_config.get("prompts") or {}).get("tool_prompt_mode", "system_once")
        max_steps = max(len(turns) + 2, replay_config.get("loop", {}).get("plan_turn_limit", 1) + len(turns))

        result = conductor.run_agentic_loop(
            "",
            user_prompt,
            model_id,
            max_steps=max_steps,
            output_json_path=None,
            stream_responses=False,
            output_md_path=None,
            tool_prompt_mode=tool_prompt_mode,
        )
        return result, context
    finally:
        if original_runtime_cls is not None:
            provider_registry.register_runtime("mock_chat", original_runtime_cls)


def compare_todo_journal(actual: List[Dict[str, Any]], expected: List[Dict[str, Any]]) -> List[str]:
    def _normalize(event: Dict[str, Any]) -> Dict[str, Any]:
        normalized = dict(event)
        normalized.pop("timestamp", None)
        normalized.pop("todo_id", None)
        payload = normalized.get("payload")
        if isinstance(payload, dict):
            payload = dict(payload)
            todo_block = payload.get("todo")
            if isinstance(todo_block, dict):
                todo_block = dict(todo_block)
                for key in ("id", "created_at", "updated_at", "version"):
                    todo_block.pop(key, None)
                payload["todo"] = todo_block
            note_block = payload.get("note")
            if isinstance(note_block, dict):
                note_block = dict(note_block)
                note_block.pop("created_at", None)
                payload["note"] = note_block
            for key in ("updated_at", "version"):
                payload.pop(key, None)
            normalized["payload"] = payload
        return normalized

    mismatches: List[str] = []
    if len(actual) != len(expected):
        mismatches.append(f"Todo journal length mismatch: actual {len(actual)} vs expected {len(expected)}")
    for idx in range(min(len(actual), len(expected))):
        actual_norm = _normalize(actual[idx])
        expected_norm = _normalize(expected[idx])
        if actual_norm != expected_norm:
            mismatches.append(f"Todo event #{idx + 1} differs: actual={actual_norm} expected={expected_norm}")
    return mismatches


def sanitize_todo_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    sanitized = {"todos": [], "order": list(payload.get("order") or []), "journal": []}

    def _clean_todo(todo: Dict[str, Any]) -> Dict[str, Any]:
        clone = dict(todo)
        for key in ("id", "created_at", "updated_at", "version"):
            clone.pop(key, None)
        refs = []
        for ref in clone.get("refs") or []:
            sanitized_ref = dict(ref)
            sanitized_ref.pop("note", None)
            refs.append(sanitized_ref)
        if refs:
            clone["refs"] = refs
        notes = []
        for note in clone.get("notes") or []:
            sanitized_note = dict(note)
            sanitized_note.pop("created_at", None)
            notes.append(sanitized_note)
        if notes:
            clone["notes"] = notes
        return clone

    for todo in payload.get("todos") or []:
        if isinstance(todo, dict):
            sanitized["todos"].append(_clean_todo(todo))

    for event in payload.get("journal") or []:
        if not isinstance(event, dict):
            continue
        normalized = dict(event)
        normalized.pop("timestamp", None)
        normalized.pop("todo_id", None)
        payload_block = normalized.get("payload")
        if isinstance(payload_block, dict):
            sanitized_payload = dict(payload_block)
            todo_block = sanitized_payload.get("todo")
            if isinstance(todo_block, dict):
                sanitized_payload["todo"] = _clean_todo(todo_block)
            note_block = sanitized_payload.get("note")
            if isinstance(note_block, dict):
                sanitized_note = dict(note_block)
                sanitized_note.pop("created_at", None)
                sanitized_payload["note"] = sanitized_note
            sanitized_payload.pop("updated_at", None)
            sanitized_payload.pop("version", None)
            normalized["payload"] = sanitized_payload
        sanitized["journal"].append(normalized)
    return sanitized


def sanitize_todo_file(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {"todos": [], "order": [], "journal": []}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        raw = {}
    if not isinstance(raw, dict):
        raw = {}
    return sanitize_todo_payload(raw)


def todo_metrics_from_sanitized(payload: Dict[str, Any]) -> Dict[str, int]:
    todos = payload.get("todos") or []
    status_counts: Dict[str, int] = {}
    for todo in todos:
        status = str(todo.get("status", "todo"))
        status_counts[status] = status_counts.get(status, 0) + 1
    total = len(todos)
    open_count = sum(status_counts.get(state, 0) for state in ("todo", "in_progress", "blocked"))
    return {
        "total": total,
        "open": open_count,
        "done": status_counts.get("done", 0),
        "canceled": status_counts.get("canceled", 0),
        "blocked": status_counts.get("blocked", 0),
        "journal_events": len(payload.get("journal") or []),
    }


def compare_workspace_files(actual: Path, golden: Path, ignore: Optional[Iterable[str]] = None) -> List[str]:
    ignore_set = {Path(item) for item in (ignore or [])}
    mismatches: List[str] = []

    def _should_ignore(rel_path: Path) -> bool:
        for ign in ignore_set:
            ign_parts = ign.parts
            rel_parts = rel_path.parts
            if len(ign_parts) <= len(rel_parts) and rel_parts[: len(ign_parts)] == ign_parts:
                return True
        return False

    for golden_file in golden.rglob("*"):
        if golden_file.is_dir():
            continue
        rel = golden_file.relative_to(golden)
        if _should_ignore(rel):
            continue
        actual_file = actual / rel
        if not actual_file.exists():
            mismatches.append(f"Missing file in replay workspace: {rel}")
            continue
        if golden_file.read_bytes() != actual_file.read_bytes():
            mismatches.append(f"Content mismatch for {rel}")

    for actual_file in actual.rglob("*"):
        if actual_file.is_dir():
            continue
        rel = actual_file.relative_to(actual)
        if _should_ignore(rel):
            continue
        golden_file = golden / rel
        if not golden_file.exists():
            mismatches.append(f"Extra file present in replay workspace: {rel}")

    return mismatches


class ReplayContext:
    """Shared context for replaying provider responses."""

    def __init__(self, turns: List[AssistantTurn], workspace_path: Path, strip_prefix: str):
        self.turns = list(turns)
        self.pointer = 0
        self.request_log: List[Dict[str, Any]] = []
        self.responses: List[Dict[str, Any]] = []
        self.workspace_path = workspace_path
        self.strip_prefix = strip_prefix

    def next_turn(self) -> AssistantTurn:
        if self.pointer < len(self.turns):
            turn = self.turns[self.pointer]
            self.pointer += 1
            return turn
        # Return an empty assistant completion when trace is exhausted
        return AssistantTurn(message_id=f"replay_done_{self.pointer}", text_parts=[""], tool_calls=[])

    def record_invocation(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]],
        provider_message: ProviderMessage,
    ) -> None:
        self.request_log.append({"messages": copy.deepcopy(messages), "tools": copy.deepcopy(tools)})
        self.responses.append(
            {
                "role": provider_message.role,
                "content": provider_message.content,
                "tool_calls": [tc.__dict__ for tc in provider_message.tool_calls],
                "finish_reason": provider_message.finish_reason,
            }
        )


class ReplayProviderRuntime(ProviderRuntime):
    """Provider runtime that returns pre-recorded assistant turns."""

    context: Optional[ReplayContext] = None

    def __init__(self, descriptor):
        super().__init__(descriptor)

    @classmethod
    def set_context(cls, ctx: ReplayContext) -> None:
        cls.context = ctx

    def create_client(self, api_key: str, *, base_url: Optional[str] = None, default_headers: Optional[Dict[str, str]] = None) -> Any:
        return {"api_key": api_key, "base_url": base_url, "headers": default_headers or {}}

    def invoke(
        self,
        *,
        client: Any,
        model: str,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]],
        stream: bool,
        context,
    ) -> ProviderResult:
        if ReplayProviderRuntime.context is None:
            raise RuntimeError("ReplayProviderRuntime context not configured.")
        turn = ReplayProviderRuntime.context.next_turn()
        tool_calls: List[ProviderToolCall] = []
        for idx, call in enumerate(turn.tool_calls):
            resolved_args = resolve_todo_placeholders(copy.deepcopy(call.input or {}), ReplayProviderRuntime.context.workspace_path)
            tool_name = call.tool
            arguments_payload = resolved_args
            if tool_name in {"write", "opencode_write", "Write"}:
                file_path = str(resolved_args.get("filePath") or resolved_args.get("file_path") or resolved_args.get("path") or "")
                normalized = normalize_path(file_path, ReplayProviderRuntime.context.strip_prefix)
                arguments_payload = {
                    "file_name": normalized,
                    "content": resolved_args.get("content", ""),
                }
                tool_name = "create_file_from_block"
            elif tool_name in {"bash", "run_shell", "Bash"}:
                tool_name = "run_shell"
                arguments_payload = {"command": resolved_args.get("command") or resolved_args.get("cmd")}

            tool_calls.append(
                ProviderToolCall(
                    id=f"replay_tool_{ReplayProviderRuntime.context.pointer}_{idx}",
                    name=tool_name,
                    arguments=json.dumps(arguments_payload or {}, sort_keys=True),
                )
            )
        content = None
        if turn.text_parts:
            content = "\n\n".join(turn.text_parts)
        finish_reason = "tool_calls" if tool_calls else "stop"
        provider_message = ProviderMessage(
            role="assistant",
            content=content,
            tool_calls=tool_calls,
            finish_reason=finish_reason,
        )
        ReplayProviderRuntime.context.record_invocation(messages, tools, provider_message)
        return ProviderResult(
            messages=[provider_message],
            raw_response={"replay": True},
            usage={"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            metadata={"replay_index": ReplayProviderRuntime.context.pointer - 1},
        )


def main() -> None:
    args = parse_args()
    config_path = Path(args.config).resolve()
    session_path = Path(args.session).resolve()
    workspace_path = Path(args.workspace).resolve()

    if not config_path.exists():
        raise FileNotFoundError(f"Config not found: {config_path}")
    if not session_path.exists():
        raise FileNotFoundError(f"Session dump not found: {session_path}")

    ensure_workspace(workspace_path)

    config = load_agent_config(str(config_path))

    hash_manifest_path: Optional[Path] = None
    if args.hash_manifest:
        hash_manifest_path = Path(args.hash_manifest).resolve()
    validate_prompt_hashes(config_path, config, hash_manifest_path)

    tool_calls = load_opencode_tool_calls(session_path)
    assistant_turns = load_opencode_turns(session_path)
    user_prompt = load_user_prompt(session_path)

    if args.limit is not None:
        tool_calls = tool_calls[: args.limit]
        assistant_turns = assistant_turns[: args.limit]

    run_result, replay_context = run_agentic_replay(
        config_path,
        config,
        assistant_turns,
        user_prompt,
        workspace_path,
    )

    # Extract actual tool results from run
    actual_messages = run_result.get("messages", [])
    actual_tool_results: List[Tuple[str, Dict[str, Any]]] = []
    for msg in actual_messages:
        if msg.get("role") == "tool_result":
            for tc in msg.get("tool_calls") or []:
                name = tc.get("function", {}).get("name")
                result_dict = tc.get("result") or {}
                actual_tool_results.append((name, result_dict))

    paths_for_prefix = [
        call.input.get("filePath") or call.input.get("file_path") or call.input.get("path")
        for call in tool_calls
        if call.tool in {"write", "opencode_write", "Write"}
    ]
    strip_prefix = derive_strip_prefix(paths_for_prefix)
    path_normalizers: set[str] = set()
    for candidate in [strip_prefix, str(workspace_path)]:
        if not candidate:
            continue
        normalized = os.path.normpath(candidate)
        path_normalizers.add(normalized)
        path_normalizers.add(normalized + os.sep)

    def _canonical_tool(name: str) -> str:
        mapping = {
            "create_file_from_block": "write",
            "opencode_write": "write",
            "write": "write",
            "Write": "write",
            "run_shell": "bash",
            "bash": "bash",
            "Bash": "bash",
            "TodoWrite": "todo.write_board",
        }
        return mapping.get(name, name)

    print("\n=== Tool Call Verification ===")
    mismatches_detected = False
    for idx, call in enumerate(tool_calls, 1):
        print(f"\n--- Tool Call #{idx}: {call.tool} ---")
        if idx - 1 >= len(actual_tool_results):
            print("Mismatch: missing actual execution result.")
            mismatches_detected = True
            continue
        actual_tool_name, actual_result = actual_tool_results[idx - 1]
        expected_tool = _canonical_tool(call.tool)
        normalized_actual_tool = _canonical_tool(actual_tool_name)
        if normalized_actual_tool != expected_tool:
            print(f"Mismatch: expected tool '{call.tool}' but replay executed '{actual_tool_name}'")
            mismatches_detected = True
        summary = summarize_result(normalized_actual_tool, actual_result)
        mismatches = compare_expected(call, summary, path_normalizers=path_normalizers)
        if mismatches:
            mismatches_detected = True
            for item in mismatches:
                print(f"- {item}")
        else:
            print("Result matches expected output.")

    if len(actual_tool_results) != len(tool_calls):
        mismatches_detected = True
        print(f"\nWarning: expected {len(tool_calls)} tool executions but replay captured {len(actual_tool_results)}.")

    # Prompt parity inspection
    print("\n=== Provider Request Hashes ===")
    request_hashes = []
    for idx, entry in enumerate(replay_context.request_log, 1):
        digest = sha256_json(entry["messages"])
        request_hashes.append(digest)
        print(f"Turn {idx:02d}: {digest}")

    # Todo normalization and summary generation
    todo_sanitized = sanitize_todo_file(workspace_path / TodoStore.FILENAME)
    todo_metrics = todo_metrics_from_sanitized(todo_sanitized)
    summary_payload = {
        "config": relative_to_root(config_path),
        "workspace": relative_to_root(workspace_path),
        "todo_metrics": todo_metrics,
        "todos_path": relative_to_root(workspace_path / TodoStore.FILENAME),
    }
    summary_path = workspace_path / "summary.json"
    summary_path.write_text(json.dumps(summary_payload, indent=2) + "\n", encoding="utf-8")
    print(f"\nWrote summary metrics to {summary_path}")

    if args.todo_expected:
        expected_path = Path(args.todo_expected).resolve()
        if not expected_path.exists():
            print(f"\nTodo expected file not found: {expected_path}")
        else:
            expected_events = load_expected_todos(expected_path)
            todo_mismatches = compare_todo_journal(todo_sanitized.get("journal", []), expected_events)
            if todo_mismatches:
                mismatches_detected = True
                print("\n=== Todo Journal Mismatches ===")
                for item in todo_mismatches:
                    print(f"- {item}")
            else:
                print("\nTodo journal matches expected events.")

    if args.golden_workspace:
        golden_workspace = Path(args.golden_workspace).resolve()
        if not golden_workspace.exists():
            print(f"\nGolden workspace not found: {golden_workspace}")
        else:
            workspace_mismatches = compare_workspace_files(
                workspace_path,
                golden_workspace,
                ignore=[".kyle/todos.json", "summary.json", ".git", "demo", "test_protofs", ".claude"],
            )
            if workspace_mismatches:
                mismatches_detected = True
                print("\n=== Workspace Mismatches ===")
                for item in workspace_mismatches:
                    print(f"- {item}")
            else:
                print("\nWorkspace files match golden snapshot.")

    print("\n=== Replay Summary ===")
    print(f"Tool call mismatches detected: {'YES' if mismatches_detected else 'NO'}")
    print(f"Captured {len(replay_context.request_log)} provider invocations.")


if __name__ == "__main__":
    main()
