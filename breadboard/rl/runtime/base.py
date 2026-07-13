from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class RuntimeHealth:
    ready: bool
    backend: str
    reasons: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ready": self.ready,
            "backend": self.backend,
            "reasons": list(self.reasons),
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class RuntimeResult:
    success: bool
    result_kind: str
    observation: dict[str, Any] | None = None
    reward: float | None = None
    done: bool = False
    error: dict[str, Any] | None = None
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "success": self.success,
            "result_kind": self.result_kind,
            "done": self.done,
            "evidence": dict(self.evidence),
        }
        if self.observation is not None:
            payload["observation"] = dict(self.observation)
        if self.reward is not None:
            payload["reward"] = self.reward
        if self.error is not None:
            payload["error"] = dict(self.error)
        return payload


@dataclass(frozen=True)
class RuntimeSnapshot:
    snapshot_id: str
    state: dict[str, Any]
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "snapshot_id": self.snapshot_id,
            "state": dict(self.state),
            "metadata": dict(self.metadata),
        }


class RuntimeBackend(Protocol):
    backend_id: str

    def health(self) -> RuntimeHealth:
        ...

    def reset(self) -> RuntimeResult:
        ...

    def observe(self) -> RuntimeResult:
        ...

    def step(self, action: dict[str, Any]) -> RuntimeResult:
        ...

    def evaluate(self) -> RuntimeResult:
        ...

    def snapshot(self) -> RuntimeSnapshot:
        ...

    def restore(self, snapshot: RuntimeSnapshot) -> RuntimeResult:
        ...

    def terminate(self) -> RuntimeResult:
        ...
