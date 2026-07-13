from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping

import yaml




@dataclass(frozen=True)
class RegistryTool:
    id: str
    name: str
    type_id: str
    binding: dict[str, Any] = field(default_factory=dict)
    classification: dict[str, Any] = field(default_factory=dict)
    aliases: tuple[str, ...] = ()
    execution: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)


def default_tool_defs_dir() -> Path:
    return Path(__file__).resolve().parents[2] / "implementations" / "tools" / "defs"


@dataclass(frozen=True)
class ToolRegistry:
    tools_by_name: dict[str, RegistryTool]
    aliases: dict[str, str]

    def resolve_name(self, name: str) -> str:
        current = str(name)
        seen: set[str] = set()
        while current in self.aliases and current not in seen:
            seen.add(current)
            current = self.aliases[current]
        return current

    def dispatch_for(self, name: str) -> dict[str, Any] | None:
        tool = self.tools_by_name.get(self.resolve_name(name))
        if tool is None:
            return None
        return dict(tool.binding)

    def guardrail_sets(self) -> dict[str, set[str]]:
        sets: dict[str, set[str]] = {}
        for tool in self.tools_by_name.values():
            for guardrail_set in tool.classification.get("guardrail_sets", []):
                sets.setdefault(str(guardrail_set), set()).add(tool.name)
        for alias, target in self.aliases.items():
            resolved = self.resolve_name(target)
            target_tool = self.tools_by_name.get(resolved)
            if target_tool is None:
                continue
            for guardrail_set in target_tool.classification.get("guardrail_sets", []):
                sets.setdefault(str(guardrail_set), set()).add(alias)
        return sets

    def nonblocking_names(self) -> set[str]:
        names = {
            tool.name
            for tool in self.tools_by_name.values()
            if not bool(tool.execution.get("blocking", False))
        }
        for alias, target in self.aliases.items():
            if self.resolve_name(target) in names:
                names.add(alias)
        return names

    def alias_map(self) -> dict[str, str]:
        return dict(self.aliases)

    def names_for_guardrail_set(self, guardrail_set: str) -> set[str]:
        return set(self.guardrail_sets().get(guardrail_set, set()))


def load_tool_registry(
    defs_dir: str | Path | None = None,
    *,
    aliases: Mapping[str, str] | None = None,
) -> ToolRegistry:
    defs_path = Path(defs_dir) if defs_dir is not None else default_tool_defs_dir()
    if not defs_path.is_dir():
        raise FileNotFoundError(f"Tools definitions directory not found: {defs_path}")

    tools_by_name: dict[str, RegistryTool] = {}
    alias_map = {str(key): str(value) for key, value in (aliases or {}).items()}

    for path in sorted(defs_path.iterdir()):
        if path.suffix not in {".yaml", ".yml"}:
            continue
        with open(path, "r", encoding="utf-8") as handle:
            data = yaml.safe_load(handle) or {}
        if not isinstance(data, dict):
            raise ValueError(f"Tool YAML must be a mapping in {path}")
        tool = _registry_tool_from_yaml(data, path)
        if tool.name in tools_by_name:
            raise ValueError(f"Duplicate tool name {tool.name!r} in {path}")
        tools_by_name[tool.name] = tool
        for alias in tool.aliases:
            alias_map.setdefault(alias, tool.name)

    return ToolRegistry(tools_by_name=tools_by_name, aliases=alias_map)


@lru_cache(maxsize=8)
def cached_tool_registry(defs_dir: str | None = None) -> ToolRegistry:
    return load_tool_registry(defs_dir)

def registry_from_config(config: Mapping[str, Any] | None) -> ToolRegistry:
    """Load the unified tool registry using a runtime config's tool settings."""
    tools_cfg = (config or {}).get("tools", {}) if isinstance(config, Mapping) else {}
    tools_cfg = tools_cfg or {}
    defs_dir = tools_cfg.get("defs_dir")
    aliases = {str(key): str(value) for key, value in (tools_cfg.get("aliases") or {}).items()}
    if not aliases:
        return cached_tool_registry(str(defs_dir) if defs_dir else None)
    return load_tool_registry(defs_dir, aliases=aliases)


def guardrail_names_for(config: Mapping[str, Any] | None, guardrail_set: str) -> set[str]:
    """Return canonical names and aliases in one guardrail classification set."""
    return registry_from_config(config).names_for_guardrail_set(guardrail_set)


def _registry_tool_from_yaml(data: Mapping[str, Any], path: str) -> RegistryTool:
    tool_id = str(data.get("id") or data.get("name") or "")
    name = str(data.get("name") or tool_id)
    if not tool_id or not name:
        raise ValueError(f"Tool YAML missing id/name in {path}")
    manipulations = [str(item) for item in (data.get("manipulations") or [])]
    execution = dict(data.get("execution") or {})
    raw_binding = data.get("binding") if isinstance(data.get("binding"), Mapping) else {}
    raw_classification = data.get("classification") if isinstance(data.get("classification"), Mapping) else {}
    aliases = tuple(str(item) for item in (data.get("aliases") or []) if str(item))
    return RegistryTool(
        id=tool_id,
        name=name,
        type_id=str(data.get("type_id") or "python"),
        binding=_binding_for(data, raw_binding),
        classification=_classification_for(name, manipulations, raw_classification),
        aliases=aliases,
        execution=execution,
        raw=dict(data),
    )


def _binding_for(data: Mapping[str, Any], raw_binding: Mapping[str, Any]) -> dict[str, Any]:
    binding = dict(raw_binding)
    binding.setdefault("handler", str(data.get("name") or data.get("id")))
    binding.setdefault("type_id", str(data.get("type_id") or "python"))
    return binding


def _classification_for(name: str, manipulations: list[str], raw_classification: Mapping[str, Any]) -> dict[str, Any]:
    classification = dict(raw_classification)
    guardrail_sets = set(str(item) for item in classification.get("guardrail_sets", []) or [])
    categories = set(str(item) for item in classification.get("categories", []) or [])
    lowered = name.lower()
    if lowered in {"list", "list_dir"} or any(item.startswith("file.list") for item in manipulations):
        guardrail_sets.add("list")
        categories.add("read_only")
    if lowered in {"read", "read_file"} or any(item.startswith("file.read") for item in manipulations):
        guardrail_sets.add("read")
        categories.add("read_only")
    if any(item.startswith("shell.") for item in manipulations) or lowered in {"bash", "run_shell", "shell_command"}:
        guardrail_sets.add("bash")
        categories.add("shell")
    if any(item.startswith(("file.write", "diff.", "file.create")) for item in manipulations) or lowered in {
        "patch",
        "write",
        "create_file",
        "create_file_from_block",
        "apply_unified_patch",
        "apply_search_replace",
    }:
        guardrail_sets.add("edit")
        categories.add("write")
    if lowered.startswith("todo") or lowered in {"update_plan", "todo.write_board"}:
        guardrail_sets.add("todo")
        categories.add("coordination")
    classification["guardrail_sets"] = sorted(guardrail_sets)
    classification["categories"] = sorted(categories)
    return classification
