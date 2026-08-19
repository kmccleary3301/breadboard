from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Mapping

from breadboard.product.runtime.artifacts import ArtifactRef

from .manifest import ReplayManifest


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise TypeError("replay plan option keys must be strings")
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    json.dumps(value, allow_nan=False)
    return value


def _plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value


def canonical_json(value: Any) -> bytes:
    return json.dumps(
        _plain(value), allow_nan=False, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def sha256_ref(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


@dataclass(frozen=True, slots=True)
class ReplayPlan:
    source_session_id: str
    input_artifact: ArtifactRef
    worker_id: str
    manifest: ReplayManifest
    transcript_path: str = "transcript.json"
    options: Mapping[str, Any] = field(default_factory=dict)
    schema_version: str = "bb.replay_plan.v1"
    _plan_id: str = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if self.schema_version != "bb.replay_plan.v1":
            raise ValueError("unsupported replay plan schema_version")
        if not isinstance(self.source_session_id, str) or not self.source_session_id:
            raise ValueError("replay plan source_session_id must be populated")
        if not isinstance(self.worker_id, str) or not self.worker_id:
            raise ValueError("replay plan worker_id must be populated")
        if not isinstance(self.input_artifact, ArtifactRef):
            raise TypeError("replay plan input_artifact must be an ArtifactRef")
        if not isinstance(self.manifest, ReplayManifest):
            raise TypeError("replay plan manifest must be a ReplayManifest")
        if self.transcript_path not in self.manifest.paths:
            raise ValueError("replay manifest must name the normalized transcript")
        frozen_options = _freeze(self.options)
        object.__setattr__(self, "options", frozen_options)
        identity = self.as_dict(include_id=False)
        object.__setattr__(self, "_plan_id", sha256_ref(canonical_json(identity)))

    @property
    def plan_id(self) -> str:
        return self._plan_id

    def as_dict(self, *, include_id: bool = True) -> dict[str, Any]:
        result = {
            "schema_version": self.schema_version,
            "source_session_id": self.source_session_id,
            "input_artifact": self.input_artifact.as_dict(),
            "worker_id": self.worker_id,
            "manifest": self.manifest.as_dict(),
            "transcript_path": self.transcript_path,
            "options": _plain(self.options),
        }
        if include_id:
            result["plan_id"] = self.plan_id
        return result
