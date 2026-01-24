from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

import yaml

from .model import GraphSkill, PromptSkill


def _load_document(path: Path) -> Any:
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".json":
        return json.loads(text)
    return yaml.safe_load(text)


def _iter_skill_files(root: Path) -> Iterable[Path]:
    if root.is_file():
        yield root
        return
    if not root.exists():
        return
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        suffix = path.suffix.lower()
        if suffix not in {".json", ".yaml", ".yml"}:
            continue
        yield path


def _normalize_string_list(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    items: List[str] = []
    for item in value:
        if isinstance(item, str) and item.strip():
            items.append(item.strip())
    return items


def _normalize_type(value: Any) -> str:
    raw = str(value or "").strip().lower()
    if raw in {"prompt", "prompt_skill", "promptskill"}:
        return "prompt"
    if raw in {"graph", "graph_skill", "graphskill"}:
        return "graph"
    return raw or "prompt"


def _parse_prompt_skill(payload: Dict[str, Any]) -> PromptSkill | None:
    skill_id = payload.get("id") or payload.get("skill_id") or payload.get("name")
    if not isinstance(skill_id, str) or not skill_id.strip():
        return None
    version = payload.get("version")
    if not isinstance(version, str) or not version.strip():
        version = "0.0.0"
    label = payload.get("label")
    if not isinstance(label, str) or not label.strip():
        label = None
    group = payload.get("group")
    if not isinstance(group, str) or not group.strip():
        group = None
    description = payload.get("description")
    if not isinstance(description, str) or not description.strip():
        description = None
    long_description = payload.get("long_description") or payload.get("longDescription")
    if not isinstance(long_description, str) or not long_description.strip():
        long_description = None
    tags = _normalize_string_list(payload.get("tags"))
    defaults = payload.get("defaults") if isinstance(payload.get("defaults"), dict) else None
    dependencies = _normalize_string_list(payload.get("dependencies"))
    conflicts = _normalize_string_list(payload.get("conflicts"))
    deprecated = bool(payload.get("deprecated", False))
    provider_constraints = payload.get("provider_constraints") if isinstance(payload.get("provider_constraints"), dict) else None
    slot = payload.get("slot") or "system"
    if not isinstance(slot, str) or slot.strip() not in {"system", "developer", "user", "per_turn"}:
        slot = "system"

    blocks: List[str] = []
    raw_blocks = payload.get("blocks")
    if isinstance(raw_blocks, list):
        for block in raw_blocks:
            if isinstance(block, str) and block.strip():
                blocks.append(block.rstrip())
    else:
        prompt_text = payload.get("prompt") or payload.get("text")
        if isinstance(prompt_text, str) and prompt_text.strip():
            blocks.append(prompt_text.rstrip())

    return PromptSkill(
        skill_id=skill_id.strip(),
        version=version.strip(),
        label=label,
        group=group,
        description=description,
        long_description=long_description,
        tags=tags,
        defaults=defaults,
        dependencies=dependencies,
        conflicts=conflicts,
        deprecated=deprecated,
        provider_constraints=provider_constraints,
        slot=slot,
        blocks=blocks,
    )


def _parse_graph_skill(payload: Dict[str, Any]) -> GraphSkill | None:
    skill_id = payload.get("id") or payload.get("skill_id") or payload.get("name")
    if not isinstance(skill_id, str) or not skill_id.strip():
        return None
    version = payload.get("version")
    if not isinstance(version, str) or not version.strip():
        version = "0.0.0"
    label = payload.get("label")
    if not isinstance(label, str) or not label.strip():
        label = None
    group = payload.get("group")
    if not isinstance(group, str) or not group.strip():
        group = None
    description = payload.get("description")
    if not isinstance(description, str) or not description.strip():
        description = None
    long_description = payload.get("long_description") or payload.get("longDescription")
    if not isinstance(long_description, str) or not long_description.strip():
        long_description = None
    tags = _normalize_string_list(payload.get("tags"))
    defaults = payload.get("defaults") if isinstance(payload.get("defaults"), dict) else None
    dependencies = _normalize_string_list(payload.get("dependencies"))
    conflicts = _normalize_string_list(payload.get("conflicts"))
    deprecated = bool(payload.get("deprecated", False))
    provider_constraints = payload.get("provider_constraints") if isinstance(payload.get("provider_constraints"), dict) else None
    determinism = payload.get("determinism")
    if not isinstance(determinism, str) or not determinism.strip():
        determinism = None

    steps: List[Dict[str, Any]] = []
    raw_steps = payload.get("steps") or payload.get("calls")
    if isinstance(raw_steps, list):
        for step in raw_steps:
            if isinstance(step, dict) and step:
                steps.append(dict(step))

    return GraphSkill(
        skill_id=skill_id.strip(),
        version=version.strip(),
        label=label,
        group=group,
        description=description,
        long_description=long_description,
        tags=tags,
        defaults=defaults,
        dependencies=dependencies,
        conflicts=conflicts,
        deprecated=deprecated,
        provider_constraints=provider_constraints,
        steps=steps,
        determinism=determinism,
    )


def parse_skills_from_payload(payload: Any) -> Tuple[List[PromptSkill], List[GraphSkill]]:
    """Parse a YAML/JSON payload into PromptSkill/GraphSkill lists.

    Accepts any of:
      - { skills: [...] }
      - { prompt_skills: [...], graph_skills: [...] }
      - [ ...skills... ]
    """
    prompt_skills: List[PromptSkill] = []
    graph_skills: List[GraphSkill] = []

    if isinstance(payload, list):
        skills_list = payload
    elif isinstance(payload, dict):
        skills_list = payload.get("skills")
        if skills_list is None:
            skills_list = []
        if isinstance(payload.get("prompt_skills"), list):
            for item in payload.get("prompt_skills") or []:
                if isinstance(item, dict):
                    item = dict(item)
                    item.setdefault("type", "prompt")
                    skills_list.append(item)
        if isinstance(payload.get("graph_skills"), list):
            for item in payload.get("graph_skills") or []:
                if isinstance(item, dict):
                    item = dict(item)
                    item.setdefault("type", "graph")
                    skills_list.append(item)
    else:
        skills_list = []

    if not isinstance(skills_list, list):
        return [], []

    for raw in skills_list:
        if not isinstance(raw, dict):
            continue
        kind = _normalize_type(raw.get("type"))
        if kind == "graph":
            skill = _parse_graph_skill(raw)
            if skill:
                graph_skills.append(skill)
        else:
            skill = _parse_prompt_skill(raw)
            if skill:
                prompt_skills.append(skill)

    return prompt_skills, graph_skills


def load_skills_from_paths(paths: List[Path]) -> Tuple[List[PromptSkill], List[GraphSkill], List[str]]:
    prompt: List[PromptSkill] = []
    graph: List[GraphSkill] = []
    errors: List[str] = []

    for root in paths:
        for path in _iter_skill_files(root):
            try:
                payload = _load_document(path)
            except Exception as exc:
                errors.append(f"{path}: failed to parse ({exc})")
                continue
            try:
                ps, gs = parse_skills_from_payload(payload)
            except Exception as exc:
                errors.append(f"{path}: failed to interpret payload ({exc})")
                continue
            prompt.extend(ps)
            graph.extend(gs)
    return prompt, graph, errors

