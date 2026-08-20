from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .model import GraphSkill, PromptSkill
from .parser import load_skills_from_paths


def _normalize_string_list(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    items: List[str] = []
    for item in value:
        if isinstance(item, str) and item.strip():
            items.append(item.strip())
    return items


def load_skills(
    config: Dict[str, Any],
    workspace: str,
    *,
    plugin_skill_paths: Optional[List[Path]] = None,
) -> Tuple[List[PromptSkill], List[GraphSkill]]:
    """Load skills from configured paths and plugins.

    Skills are disabled unless `config.skills.enabled: true`.

    Supported roots:
      - `config.skills.paths`: list of files/dirs (relative to workspace)
      - `plugin_skill_paths`: list of files/dirs (already resolved)
    """
    skills_cfg = (config or {}).get("skills") if isinstance(config, dict) else None
    if not isinstance(skills_cfg, dict) or not skills_cfg.get("enabled"):
        return [], []

    roots: List[Path] = []

    raw_paths = skills_cfg.get("paths")
    if isinstance(raw_paths, list):
        for raw in raw_paths:
            if not isinstance(raw, str) or not raw.strip():
                continue
            tokenized = raw.replace("{workspace}", str(workspace)).replace("{home}", str(Path.home()))
            expanded = os.path.expanduser(os.path.expandvars(tokenized.strip()))
            path = Path(expanded)
            if not path.is_absolute():
                path = (Path(str(workspace)) / path).resolve()
            roots.append(path)

    if plugin_skill_paths:
        for root in plugin_skill_paths:
            try:
                roots.append(Path(str(root)))
            except Exception:
                continue

    # Deterministic order + first-wins de-dupe for roots.
    seen_roots: set[str] = set()
    deduped_roots: List[Path] = []
    for root in roots:
        key = str(root)
        if key in seen_roots:
            continue
        seen_roots.add(key)
        deduped_roots.append(root)

    prompt, graph, _errors = load_skills_from_paths(deduped_roots)

    # First-wins de-dupe across (id, version), per type.
    prompt_seen: set[tuple[str, str]] = set()
    prompt_out: List[PromptSkill] = []
    for skill in prompt:
        key = (skill.skill_id, skill.version)
        if key in prompt_seen:
            continue
        prompt_seen.add(key)
        prompt_out.append(skill)

    graph_seen: set[tuple[str, str]] = set()
    graph_out: List[GraphSkill] = []
    for skill in graph:
        key = (skill.skill_id, skill.version)
        if key in graph_seen:
            continue
        graph_seen.add(key)
        graph_out.append(skill)

    return prompt_out, graph_out


def build_skill_catalog(
    prompt_skills: List[PromptSkill],
    graph_skills: List[GraphSkill],
    *,
    selection: Optional[Dict[str, Any]] = None,
    enabled_map: Optional[Dict[str, bool]] = None,
) -> Dict[str, Any]:
    """Build the TUI-facing skills catalog payload."""
    enabled_map = enabled_map or {}
    selection = selection or {}

    def _enabled(skill_id: str, version: str) -> bool:
        key = f"{skill_id}@{version}"
        return bool(enabled_map.get(key, True))

    skills: List[Dict[str, Any]] = []
    for skill in prompt_skills:
        version = getattr(skill, "version", "0.0.0") or "0.0.0"
        skills.append(
            {
                "id": skill.skill_id,
                "type": "prompt",
                "version": version,
                "label": getattr(skill, "label", None),
                "group": getattr(skill, "group", None),
                "description": getattr(skill, "description", None),
                "long_description": getattr(skill, "long_description", None),
                "tags": list(getattr(skill, "tags", []) or []),
                "defaults": getattr(skill, "defaults", None),
                "dependencies": list(getattr(skill, "dependencies", []) or []),
                "conflicts": list(getattr(skill, "conflicts", []) or []),
                "deprecated": bool(getattr(skill, "deprecated", False)),
                "provider_constraints": getattr(skill, "provider_constraints", None),
                "slot": getattr(skill, "slot", None),
                "enabled": _enabled(skill.skill_id, version),
            }
        )
    for skill in graph_skills:
        version = getattr(skill, "version", "0.0.0") or "0.0.0"
        steps = getattr(skill, "steps", []) or []
        skills.append(
            {
                "id": skill.skill_id,
                "type": "graph",
                "version": version,
                "label": getattr(skill, "label", None),
                "group": getattr(skill, "group", None),
                "description": getattr(skill, "description", None),
                "long_description": getattr(skill, "long_description", None),
                "tags": list(getattr(skill, "tags", []) or []),
                "defaults": getattr(skill, "defaults", None),
                "dependencies": list(getattr(skill, "dependencies", []) or []),
                "conflicts": list(getattr(skill, "conflicts", []) or []),
                "deprecated": bool(getattr(skill, "deprecated", False)),
                "provider_constraints": getattr(skill, "provider_constraints", None),
                "steps": len(steps) if isinstance(steps, list) else None,
                "determinism": getattr(skill, "determinism", None),
                "enabled": _enabled(skill.skill_id, version),
            }
        )

    return {
        "catalog_version": "skills-v1",
        "selection": selection,
        "skills": skills,
        # Optional deeper payload for debugging (kept for future expansion)
        "prompt_skills": [s.__dict__ for s in prompt_skills],
        "graph_skills": [s.__dict__ for s in graph_skills],
    }


def normalize_skill_selection(config: Dict[str, Any], selection: Any) -> Dict[str, Any]:
    """Normalize selection payload and merge with config defaults."""
    skills_cfg = (config or {}).get("skills") if isinstance(config, dict) else None
    base: Dict[str, Any] = {}
    if isinstance(skills_cfg, dict):
        mode = skills_cfg.get("mode")
        if isinstance(mode, str) and mode.strip().lower() in {"allowlist", "blocklist"}:
            base["mode"] = mode.strip().lower()
        base["allowlist"] = _normalize_string_list(skills_cfg.get("allowlist"))
        base["blocklist"] = _normalize_string_list(skills_cfg.get("blocklist"))
        profile = skills_cfg.get("profile")
        if isinstance(profile, str) and profile.strip():
            base["profile"] = profile.strip()

    incoming: Dict[str, Any] = dict(selection) if isinstance(selection, dict) else {}
    if "selected" in incoming and "allowlist" not in incoming:
        incoming["allowlist"] = incoming.get("selected")

    merged: Dict[str, Any] = dict(base)
    merged.update({k: v for k, v in incoming.items() if v is not None})

    allowlist_norm = _normalize_string_list(merged.get("allowlist"))
    blocklist_norm = _normalize_string_list(merged.get("blocklist"))
    mode = merged.get("mode")
    if isinstance(mode, str) and mode.strip().lower() in {"allowlist", "blocklist"}:
        mode_norm = mode.strip().lower()
    else:
        mode_norm = "allowlist" if allowlist_norm and not blocklist_norm else "blocklist"

    return {
        "mode": mode_norm,
        "allowlist": allowlist_norm,
        "blocklist": blocklist_norm,
        "profile": merged.get("profile"),
    }


def apply_skill_selection(
    prompt_skills: List[PromptSkill],
    graph_skills: List[GraphSkill],
    selection: Dict[str, Any],
) -> Tuple[List[PromptSkill], List[GraphSkill], Dict[str, bool]]:
    """Apply allowlist/blocklist selection.

    Semantics:
      - allowlist: selected = enabled
      - blocklist: selected = disabled
    """
    selection = selection or {}
    mode = str(selection.get("mode") or "blocklist").strip().lower()
    allowset = set(_normalize_string_list(selection.get("allowlist")))
    blockset = set(_normalize_string_list(selection.get("blocklist")))

    def _is_marked(skill_id: str, version: str) -> bool:
        key = f"{skill_id}@{version}"
        if mode == "allowlist":
            return (skill_id in allowset) or (key in allowset)
        return (skill_id in blockset) or (key in blockset)

    enabled_map: Dict[str, bool] = {}
    selected_prompts: List[PromptSkill] = []
    selected_graphs: List[GraphSkill] = []

    for skill in prompt_skills:
        version = getattr(skill, "version", "0.0.0") or "0.0.0"
        key = f"{skill.skill_id}@{version}"
        marked = _is_marked(skill.skill_id, version)
        enabled = bool(marked) if mode == "allowlist" else not bool(marked)
        enabled_map[key] = enabled
        if enabled:
            selected_prompts.append(skill)

    for skill in graph_skills:
        version = getattr(skill, "version", "0.0.0") or "0.0.0"
        key = f"{skill.skill_id}@{version}"
        marked = _is_marked(skill.skill_id, version)
        enabled = bool(marked) if mode == "allowlist" else not bool(marked)
        enabled_map[key] = enabled
        if enabled:
            selected_graphs.append(skill)

    return selected_prompts, selected_graphs, enabled_map

