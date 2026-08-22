from __future__ import annotations

import heapq
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple


class ExtensionPhase(str, Enum):
    ON_SESSION_INIT = "on_session_init"
    ON_TURN_START = "on_turn_start"
    BEFORE_MODEL = "before_model"
    AFTER_MODEL = "after_model"
    BEFORE_TOOL = "before_tool"
    AFTER_TOOL = "after_tool"
    BEFORE_EMIT = "before_emit"
    ON_TURN_END = "on_turn_end"
    ON_SESSION_END = "on_session_end"


PHASE_ORDER: Tuple[str, ...] = (
    ExtensionPhase.ON_SESSION_INIT.value,
    ExtensionPhase.ON_TURN_START.value,
    ExtensionPhase.BEFORE_MODEL.value,
    ExtensionPhase.AFTER_MODEL.value,
    ExtensionPhase.BEFORE_TOOL.value,
    ExtensionPhase.AFTER_TOOL.value,
    ExtensionPhase.BEFORE_EMIT.value,
    ExtensionPhase.ON_TURN_END.value,
    ExtensionPhase.ON_SESSION_END.value,
)

_PHASE_INDEX = {phase: idx for idx, phase in enumerate(PHASE_ORDER)}


def normalize_phase(value: str | ExtensionPhase) -> str:
    phase = value.value if isinstance(value, ExtensionPhase) else str(value)
    if phase not in _PHASE_INDEX:
        raise ValueError(f"Unknown extension phase: {value}")
    return phase


@dataclass(frozen=True)
class MiddlewareSpec:
    middleware_id: str
    plugin_id: str
    phase: str | ExtensionPhase
    priority: int = 100
    before: Tuple[str, ...] = field(default_factory=tuple)
    after: Tuple[str, ...] = field(default_factory=tuple)
    handler: Optional[Callable[..., Any]] = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "middleware_id", str(self.middleware_id or "").strip())
        object.__setattr__(self, "plugin_id", str(self.plugin_id or "").strip())
        object.__setattr__(self, "phase", normalize_phase(self.phase))
        object.__setattr__(self, "priority", int(self.priority))
        before = tuple(str(item).strip() for item in (self.before or ()) if str(item).strip())
        after = tuple(str(item).strip() for item in (self.after or ()) if str(item).strip())
        object.__setattr__(self, "before", before)
        object.__setattr__(self, "after", after)
        if not self.middleware_id:
            raise ValueError("middleware_id is required")
        if not self.plugin_id:
            raise ValueError("plugin_id is required")


def _priority_key(spec: MiddlewareSpec) -> Tuple[int, str, str]:
    return (spec.priority, spec.plugin_id, spec.middleware_id)


def _topo_sort_phase(specs: Sequence[MiddlewareSpec]) -> List[MiddlewareSpec]:
    by_id: Dict[str, MiddlewareSpec] = {}
    for spec in specs:
        if spec.middleware_id in by_id:
            raise ValueError(f"Duplicate middleware_id in phase: {spec.middleware_id}")
        by_id[spec.middleware_id] = spec

    edges: Dict[str, set[str]] = {spec_id: set() for spec_id in by_id}
    indegree: Dict[str, int] = {spec_id: 0 for spec_id in by_id}

    def add_edge(src: str, dst: str) -> None:
        if src == dst:
            raise ValueError(f"Middleware dependency cycle on {src}")
        if dst not in by_id:
            raise ValueError(f"Unknown middleware dependency: {dst}")
        if dst not in edges[src]:
            edges[src].add(dst)
            indegree[dst] += 1

    for spec in specs:
        for before_id in spec.before:
            add_edge(spec.middleware_id, before_id)
        for after_id in spec.after:
            add_edge(after_id, spec.middleware_id)

    heap: List[Tuple[Tuple[int, str, str], str]] = []
    for spec_id, spec in by_id.items():
        if indegree[spec_id] == 0:
            heapq.heappush(heap, (_priority_key(spec), spec_id))

    ordered: List[MiddlewareSpec] = []
    while heap:
        _, spec_id = heapq.heappop(heap)
        spec = by_id[spec_id]
        ordered.append(spec)
        for neighbor in sorted(edges[spec_id], key=lambda nid: _priority_key(by_id[nid])):
            indegree[neighbor] -= 1
            if indegree[neighbor] == 0:
                heapq.heappush(heap, (_priority_key(by_id[neighbor]), neighbor))

    if len(ordered) != len(specs):
        raise ValueError("Middleware dependency cycle detected")
    return ordered


def order_middleware(specs: Sequence[MiddlewareSpec]) -> List[MiddlewareSpec]:
    by_phase: Dict[str, List[MiddlewareSpec]] = {phase: [] for phase in PHASE_ORDER}
    for spec in specs:
        phase = normalize_phase(spec.phase)
        by_phase[phase].append(spec)

    ordered: List[MiddlewareSpec] = []
    for phase in PHASE_ORDER:
        phase_specs = by_phase[phase]
        if phase_specs:
            ordered.extend(_topo_sort_phase(phase_specs))
    return ordered
