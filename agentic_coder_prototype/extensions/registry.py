from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, List

from .spine import MiddlewareSpec, normalize_phase, order_middleware


@dataclass
class ExtensionRegistry:
    """Registry for extension middleware specs (deterministic ordering)."""

    middleware: List[MiddlewareSpec] = field(default_factory=list)

    def add_middleware(self, spec: MiddlewareSpec) -> None:
        self.middleware.append(spec)

    def extend(self, specs: Iterable[MiddlewareSpec]) -> None:
        for spec in specs:
            self.add_middleware(spec)

    def list_all(self) -> List[MiddlewareSpec]:
        return list(self.middleware)

    def list_phase(self, phase: str) -> List[MiddlewareSpec]:
        phase_norm = normalize_phase(phase)
        candidates = [spec for spec in self.middleware if normalize_phase(spec.phase) == phase_norm]
        return order_middleware(candidates)
