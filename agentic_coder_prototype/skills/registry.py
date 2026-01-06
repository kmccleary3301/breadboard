from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class PromptSkill:
    skill_id: str
    description: str | None = None


@dataclass
class GraphSkill:
    skill_id: str
    description: str | None = None


def load_skills(
    config: Dict[str, Any],
    workspace: str,
    *,
    plugin_skill_paths: Optional[List[Path]] = None,
) -> Tuple[List[PromptSkill], List[GraphSkill]]:
    # Skills are disabled by default unless explicitly configured.
    skills_cfg = (config or {}).get("skills") if isinstance(config, dict) else None
    if not isinstance(skills_cfg, dict) or not skills_cfg.get("enabled"):
        return [], []
    return [], []


def build_skill_catalog(
    prompt_skills: List[PromptSkill],
    graph_skills: List[GraphSkill],
    *,
    selection: Optional[Dict[str, Any]] = None,
    enabled_map: Optional[Dict[str, bool]] = None,
) -> Dict[str, Any]:
    return {
        "prompt": [s.skill_id for s in prompt_skills],
        "graph": [s.skill_id for s in graph_skills],
        "selection": selection or {},
        "enabled": enabled_map or {},
    }


def normalize_skill_selection(config: Dict[str, Any], selection: Any) -> Dict[str, Any]:
    if isinstance(selection, dict):
        return dict(selection)
    return {}


def apply_skill_selection(
    prompt_skills: List[PromptSkill],
    graph_skills: List[GraphSkill],
    selection: Dict[str, Any],
) -> Tuple[List[PromptSkill], List[GraphSkill], Dict[str, bool]]:
    enabled: Dict[str, bool] = {}
    for skill in prompt_skills:
        enabled[skill.skill_id] = True
    for skill in graph_skills:
        enabled[skill.skill_id] = True
    return list(prompt_skills), list(graph_skills), enabled

