"""Codex a7dff904 core-skills loader.rs and render.rs model-visible metadata."""
from __future__ import annotations

from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
import json
from pathlib import Path

import yaml

_TEMPLATES = json.loads((Path(__file__).parent / "assets/codex_0_139/skill_templates.json").read_text())


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    path: Path
    root: Path
    rank: int


def load_skills(roots: list[tuple[Path, int]], disabled: set[Path]) -> list[Skill]:
    # loader.rs:478-619 scans bounded directories, skips hidden names and stops
    # descending once a SKILL.md is found. System .system is its own root.
    found: dict[Path, Skill] = {}
    for root, rank in roots:
        root = root.resolve()
        if not root.is_dir():
            continue
        pending = deque([(root, 0)])
        visited: set[Path] = set()
        while pending and len(visited) < 2000:
            folder, depth = pending.popleft()
            resolved = folder.resolve()
            if resolved in visited:
                continue
            visited.add(resolved)
            source = folder / "SKILL.md"
            if source.is_file():
                try:
                    raw = source.read_text(encoding="utf-8")
                    if not raw.startswith("---"):
                        continue
                    metadata = yaml.safe_load(raw.split("---", 2)[1])
                    if not isinstance(metadata, dict):
                        continue
                    name = metadata.get("name")
                    if name is None:
                        name = folder.name
                    description = metadata.get("description", "")
                    if not isinstance(name, str) or not isinstance(description, str):
                        continue
                    name, description = " ".join(name.split()), " ".join(description.split())
                    if not name or len(name) > 64 or len(description) > 1024:
                        continue
                    policy_file = folder / "agents/openai.yaml"
                    policy = yaml.safe_load(policy_file.read_text()) if policy_file.is_file() else {}
                    policy = (policy or {}).get("policy") or {}
                    if policy.get("allow_implicit_invocation") is False:
                        continue
                    products = policy.get("products") or []
                    if products and "codex" not in products:
                        continue
                    path = source.resolve()
                    if path not in disabled:
                        found.setdefault(path, Skill(name, description, path, root, rank))
                except (OSError, UnicodeError, yaml.YAMLError, IndexError):
                    # Stock records invalid skills as load errors and excludes them.
                    continue
                continue
            if depth < 6:
                for child in sorted(folder.iterdir()):
                    if not child.name.startswith(".") and child.is_dir():
                        pending.append((child, depth + 1))
    return sorted(found.values(), key=lambda item: (item.rank, item.name, str(item.path)))


def _cost(text: str) -> int:
    return (len(text.encode("utf-8")) + 3) // 4


def _line(skill: Skill, path: str, description: str) -> str:
    return f"- {skill.name}: {description + ' ' if description else ''}(file: {path})"


def _body(roots: list[str], lines: list[str]) -> str:
    suffix = "ALIASES" if roots else "ABSOLUTE_PATHS"
    parts = ["## Skills", _TEMPLATES["SKILLS_INTRO_WITH_" + suffix]]
    if roots:
        parts.extend(["### Skill roots", *roots])
    parts.extend(["### Available skills", *lines, "### How to use skills", _TEMPLATES["SKILLS_HOW_TO_USE_WITH_" + suffix]])
    return "\n" + "\n".join(parts) + "\n"


def _render(skills: list[Skill], paths: list[str], budget: int, cost: Callable[[str], int]) -> tuple[list[str], int]:
    full = [_line(skill, path, skill.description) for skill, path in zip(skills, paths)]
    if sum(cost(line + "\n") for line in full) <= budget:
        return full, 0
    minimum = [_line(skill, path, "") for skill, path in zip(skills, paths)]
    costs = [cost(line + "\n") for line in minimum]
    if sum(costs) > budget:
        result = []
        for line, cost in zip(minimum, costs):
            if cost <= budget:
                result.append(line)
                budget -= cost
        return result, sum(len(skill.description) for skill in skills)
    remaining = budget - sum(costs)
    allocation = [0] * len(skills)
    extra_cost = [0] * len(skills)
    while True:
        changed = False
        for index, skill in enumerate(skills):
            if allocation[index] == len(skill.description):
                continue
            next_count = allocation[index] + 1
            new_cost = cost(_line(skill, paths[index], skill.description[:next_count]) + "\n") - costs[index]
            delta = new_cost - extra_cost[index]
            if delta <= remaining:
                allocation[index] = next_count
                extra_cost[index] = new_cost
                remaining -= delta
                changed = True
        if not changed:
            break
    return ([_line(skill, path, skill.description[:count]) for skill, path, count in zip(skills, paths, allocation)],
            sum(len(skill.description) - count for skill, count in zip(skills, allocation)))


def render_skills(skills: list[Skill], roots: list[tuple[Path, int]], context_window: int | None) -> str | None:
    if not skills:
        return None
    # render.rs:143-158: 2% of a known window in tokens, else 8000 characters.
    budget, cost = (max(context_window * 2 // 100, 1), _cost) if context_window else (8000, len)
    paths = [str(skill.path) for skill in skills]
    absolute, truncated = _render(skills, paths, budget, cost)
    if not truncated and len(absolute) == len(skills):
        return "<skills_instructions>" + _body([], absolute) + "</skills_instructions>"
    used = []
    for root, _ in roots:
        root = root.resolve()
        if root not in used and any(skill.root == root for skill in skills):
            used.append(root)
    root_lines = [f"- `r{index}` = `{root}`" for index, root in enumerate(used)]
    overhead = max(cost(_body(root_lines, [])) - cost(_body([], [])), 0)
    if overhead < budget:
        aliases = {root: f"r{index}" for index, root in enumerate(used)}
        aliased_paths = [aliases[skill.root] + "/" + skill.path.relative_to(skill.root).as_posix() for skill in skills]
        aliased, alias_truncated = _render(skills, aliased_paths, budget - overhead, cost)
        better = (len(aliased) > len(absolute) or
                  (len(aliased) == len(absolute) and (alias_truncated < truncated or
                   (alias_truncated == truncated and overhead + sum(cost(line + "\n") for line in aliased) <
                    sum(cost(line + "\n") for line in absolute)))))
        if better:
            return "<skills_instructions>" + _body(root_lines, aliased) + "</skills_instructions>"
    return "<skills_instructions>" + _body([], absolute) + "</skills_instructions>"
