from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class SkillBase:
    skill_id: str
    version: str = "0.0.0"
    label: Optional[str] = None
    group: Optional[str] = None
    description: Optional[str] = None
    long_description: Optional[str] = None
    tags: List[str] = field(default_factory=list)
    defaults: Optional[Dict[str, Any]] = None
    dependencies: List[str] = field(default_factory=list)
    conflicts: List[str] = field(default_factory=list)
    deprecated: bool = False
    provider_constraints: Optional[Dict[str, Any]] = None


@dataclass(frozen=True)
class PromptSkill(SkillBase):
    slot: str = "system"  # system|developer|user|per_turn
    blocks: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class GraphSkill(SkillBase):
    steps: List[Dict[str, Any]] = field(default_factory=list)
    determinism: Optional[str] = None

