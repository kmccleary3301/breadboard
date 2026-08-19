from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Mapping

from breadboard.product.runtime.artifacts import ArtifactRef

from .execution import ReplayExecution
from .plan import ReplayPlan


@dataclass(frozen=True, slots=True)
class ReplayRunResult:
    disposition: str
    plan_id: str
    execution: ReplayExecution | None
    artifacts: Mapping[str, ArtifactRef] = field(default_factory=dict)
    error: str | None = None

    def __post_init__(self) -> None:
        if self.disposition not in {"executed", "reused", "stored"}:
            raise ValueError("unsupported replay result disposition")
        if not isinstance(self.plan_id, str) or not self.plan_id.startswith("sha256:"):
            raise ValueError("replay result requires a canonical plan_id")
        if self.disposition == "stored" and self.execution is not None:
            raise ValueError("stored-only replay result cannot manufacture execution history")
        if self.disposition != "stored" and not isinstance(self.execution, ReplayExecution):
            raise ValueError("executed and reused results require execution provenance")
        if self.execution is not None and self.execution.plan_id != self.plan_id:
            raise ValueError("replay result execution does not match its plan")
        if self.error is not None and (not isinstance(self.error, str) or not self.error):
            raise ValueError("replay result error must be populated when present")
        if any(not isinstance(name, str) or not isinstance(ref, ArtifactRef) for name, ref in self.artifacts.items()):
            raise TypeError("replay result artifacts must be named ArtifactRef values")
        object.__setattr__(self, "artifacts", MappingProxyType(dict(sorted(self.artifacts.items()))))

    @property
    def claimable(self) -> bool:
        return (
            self.disposition in {"executed", "reused"}
            and self.execution is not None
            and self.execution.claimable
            and not self.error
        )

    def require_claimable(self) -> None:
        if not self.claimable:
            raise RuntimeError(f"{self.disposition} replay result is not claimable")


class ReplayAdmission:
    """Select reuse only when immutable plan identity and execution provenance agree."""

    def decide(
        self,
        plan: ReplayPlan,
        *,
        reuse_candidate: ReplayRunResult | None = None,
        stored_artifacts: Mapping[str, ArtifactRef] | None = None,
        execute: bool = True,
    ) -> str:
        if reuse_candidate is not None and reuse_candidate.plan_id == plan.plan_id and reuse_candidate.claimable:
            plan.manifest.validate_artifacts(reuse_candidate.artifacts)
            return "reuse"
        if execute:
            return "execute"
        if stored_artifacts:
            plan.manifest.validate_artifacts(stored_artifacts)
            return "stored"
        raise RuntimeError("replay execution is disabled and no reusable or stored result is available")
