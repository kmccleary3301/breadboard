from __future__ import annotations

import os
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

import ray

from .sandbox_v2 import DevSandboxV2


@dataclass(frozen=True)
class SandboxLaunchSpec:
    """Input for creating a sandbox actor."""

    driver: str
    image: str
    workspace: str
    session_id: str = field(default_factory=lambda: f"sb-{uuid.uuid4()}")
    name: Optional[str] = None
    lsp_actor: Any = None
    driver_options: Dict[str, Any] = field(default_factory=dict)


def resolve_driver_from_env(default: str = "process") -> str:
    """Resolve sandbox driver name from environment (best-effort)."""
    explicit = os.environ.get("BREADBOARD_SANDBOX_DRIVER") or os.environ.get("SANDBOX_DRIVER")
    if explicit and explicit.strip():
        return explicit.strip().lower()
    use_docker = os.environ.get("RAY_USE_DOCKER_SANDBOX")
    if isinstance(use_docker, str) and use_docker.strip().lower() in {"1", "true", "yes"}:
        return "docker"
    return default


def create_sandbox(spec: SandboxLaunchSpec) -> ray.actor.ActorHandle:
    """Create a sandbox actor handle for the requested driver.

    This function intentionally returns a Ray actor handle (not a local proxy).
    Local-mode wrapping is handled by the caller (see agentic_coder_prototype.utils.local_ray).
    """

    driver = (spec.driver or "process").strip().lower()
    actor_name = spec.name or f"sb-{spec.session_id}"

    if driver in {"process", "light", "dev"}:
        return DevSandboxV2.options(name=actor_name).remote(
            image=spec.image,
            session_id=spec.session_id,
            workspace=str(spec.workspace),
            lsp_actor=spec.lsp_actor,
        )

    if driver == "docker":
        from .sandbox_docker import DockerSandboxV2

        opts = dict(spec.driver_options or {})
        network = str(opts.get("network") or os.environ.get("BREADBOARD_DOCKER_NETWORK") or "none")
        runtime = opts.get("runtime") or os.environ.get("RAY_DOCKER_RUNTIME")
        docker_bin = opts.get("docker_bin") or opts.get("dockerBin") or os.environ.get("BREADBOARD_DOCKER_BIN")
        return DockerSandboxV2.options(name=actor_name).remote(
            image=spec.image,
            session_id=spec.session_id,
            workspace=str(spec.workspace),
            lsp_actor=spec.lsp_actor,
            network=network,
            runtime=runtime,
            docker_bin=docker_bin,
        )

    if driver == "none":
        # Legacy alias: treat as process for now.
        return DevSandboxV2.options(name=actor_name).remote(
            image=spec.image,
            session_id=spec.session_id,
            workspace=str(spec.workspace),
            lsp_actor=spec.lsp_actor,
        )

    # Default fallback: process sandbox.
    return DevSandboxV2.options(name=actor_name).remote(
        image=spec.image,
        session_id=spec.session_id,
        workspace=str(spec.workspace),
        lsp_actor=spec.lsp_actor,
    )
