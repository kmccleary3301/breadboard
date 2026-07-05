from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

from breadboard.rl.env_package.schema import EnvPackage


def _stable_hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class RuntimeSignature:
    package_id: str
    package_hash: str
    backend: str
    image_digest: str | None
    taskset_id: str
    source_hash: str
    hardening_policy_hash: str
    renderer_hash: str
    network_policy: str
    resource_class: str = "cpu_local"
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "package_id": self.package_id,
            "package_hash": self.package_hash,
            "backend": self.backend,
            "image_digest": self.image_digest,
            "taskset_id": self.taskset_id,
            "source_hash": self.source_hash,
            "hardening_policy_hash": self.hardening_policy_hash,
            "renderer_hash": self.renderer_hash,
            "network_policy": self.network_policy,
            "resource_class": self.resource_class,
            "metadata": dict(self.metadata),
        }

    def digest(self) -> str:
        return _stable_hash(self.to_dict())


def build_runtime_signature(package: EnvPackage, *, resource_class: str = "cpu_local") -> RuntimeSignature:
    taskset = package.tasksets[0]
    hardening_policy_hash = (
        _stable_hash(package.hardening.to_dict()) if package.hardening is not None else "sha256:no-hardening"
    )
    renderer_hash = _stable_hash(package.renderer.to_dict())
    return RuntimeSignature(
        package_id=package.package_id,
        package_hash=package.package_hash or "",
        backend=package.runtime.backend,
        image_digest=package.runtime.image_digest,
        taskset_id=taskset.taskset_id,
        source_hash=taskset.source_hash,
        hardening_policy_hash=hardening_policy_hash,
        renderer_hash=renderer_hash,
        network_policy=package.runtime.network,
        resource_class=resource_class,
    )
