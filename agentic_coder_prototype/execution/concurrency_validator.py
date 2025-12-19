"""
Validation utilities for Phase 5 concurrency policies.

Ensures that YAML configs use a consistent schema:
- nonblocking_tools / at_most_one_of: lists of tool identifiers
- groups: non-empty match lists, positive max_parallel, optional barrier_after
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional


class ConcurrencyConfigError(ValueError):
    """Raised when a concurrency policy fails validation."""


@dataclass
class _GroupSpec:
    name: Optional[str]
    match_tools: List[str]
    max_parallel: int
    barrier_after: Optional[str]


def _coerce_string_list(raw: Any, field: str) -> List[str]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ConcurrencyConfigError(f"{field} must be a list of tool ids")
    items: List[str] = []
    for idx, item in enumerate(raw):
        if not isinstance(item, str):
            raise ConcurrencyConfigError(f"{field}[{idx}] must be a string")
        stripped = item.strip()
        if not stripped:
            raise ConcurrencyConfigError(f"{field}[{idx}] cannot be empty")
        if stripped not in items:
            items.append(stripped)
    return items


def _coerce_groups(raw: Any) -> List[_GroupSpec]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ConcurrencyConfigError("groups must be a list of objects")

    groups: List[_GroupSpec] = []
    seen_names: set[str] = set()

    for idx, entry in enumerate(raw):
        if not isinstance(entry, dict):
            raise ConcurrencyConfigError(f"groups[{idx}] must be a mapping")

        name = entry.get("name")
        if name is not None:
            if not isinstance(name, str):
                raise ConcurrencyConfigError(f"groups[{idx}].name must be a string")
            name = name.strip()
            if not name:
                raise ConcurrencyConfigError(f"groups[{idx}].name cannot be empty")
            if name in seen_names:
                raise ConcurrencyConfigError(f"duplicate group name '{name}'")
            seen_names.add(name)

        match_tools = _coerce_string_list(entry.get("match_tools"), f"groups[{idx}].match_tools")
        if not match_tools:
            raise ConcurrencyConfigError(f"groups[{idx}].match_tools must include at least one tool id")

        max_parallel = entry.get("max_parallel", 1)
        if not isinstance(max_parallel, int):
            raise ConcurrencyConfigError(f"groups[{idx}].max_parallel must be an integer")
        if max_parallel < 1:
            raise ConcurrencyConfigError(f"groups[{idx}].max_parallel must be >= 1")

        barrier_after = entry.get("barrier_after")
        if barrier_after is not None:
            if not isinstance(barrier_after, str):
                raise ConcurrencyConfigError(f"groups[{idx}].barrier_after must be a string")
            barrier_after = barrier_after.strip()
            if not barrier_after:
                raise ConcurrencyConfigError(f"groups[{idx}].barrier_after cannot be empty")
            if barrier_after not in match_tools:
                raise ConcurrencyConfigError(
                    f"groups[{idx}].barrier_after must reference a tool listed in match_tools"
                )

        groups.append(
            _GroupSpec(
                name=name,
                match_tools=match_tools,
                max_parallel=max_parallel,
                barrier_after=barrier_after,
            )
        )

    return groups


def validate_concurrency_config(cfg: Any) -> Dict[str, Any]:
    """Validate and normalize the raw concurrency configuration."""
    if cfg in (None, False):
        return {}
    if not isinstance(cfg, dict):
        raise ConcurrencyConfigError("concurrency must be a mapping when provided")

    normalized: Dict[str, Any] = {}
    normalized["nonblocking_tools"] = _coerce_string_list(cfg.get("nonblocking_tools"), "nonblocking_tools")
    normalized["at_most_one_of"] = _coerce_string_list(cfg.get("at_most_one_of"), "at_most_one_of")

    group_specs = _coerce_groups(cfg.get("groups"))
    if group_specs:
        normalized["groups"] = [
            {
                "name": spec.name,
                "match_tools": spec.match_tools,
                "max_parallel": spec.max_parallel,
                "barrier_after": spec.barrier_after,
            }
            for spec in group_specs
        ]
    else:
        normalized["groups"] = []

    return normalized
