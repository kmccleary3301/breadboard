from __future__ import annotations

import os
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

import ray

from .sandbox import DevSandboxV2


@dataclass(frozen=True)
class SandboxLaunchSpec:
    """Input for creating a development-only historical sandbox actor."""

    driver: str
    image: str
    workspace: str
    session_id: str = field(default_factory=lambda: f"sb-{uuid.uuid4()}")
    name: Optional[str] = None
    lsp_actor: Any = None
    driver_options: Dict[str, Any] = field(default_factory=dict)
    protected_paths: tuple[str, ...] = ()


class SandboxDriverError(RuntimeError):
    """Typed failure for the development-only historical selector."""

    def __init__(self, message: str, *, code: str, driver: str) -> None:
        super().__init__(message)
        self.code = code
        self.driver = driver


def resolve_driver_from_env(default: str = "light") -> str:
    """Resolve a driver for explicitly development-only callers."""
    explicit = os.environ.get("BREADBOARD_SANDBOX_DRIVER") or os.environ.get(
        "SANDBOX_DRIVER"
    )
    if explicit and explicit.strip():
        return explicit.strip().lower()
    use_docker = os.environ.get("RAY_USE_DOCKER_SANDBOX")
    if isinstance(use_docker, str) and use_docker.strip().lower() in {
        "1",
        "true",
        "yes",
    }:
        return "docker"
    return default


def create_sandbox(spec: SandboxLaunchSpec) -> ray.actor.ActorHandle:
    """Create a historical actor only for explicit development selection."""
    driver = spec.driver.strip().lower() if type(spec.driver) is str else ""
    if not driver:
        raise SandboxDriverError(
            "sandbox driver must be selected explicitly",
            code="driver_unavailable",
            driver=driver,
        )
    actor_name = spec.name or f"sb-{spec.session_id}"

    if driver in {"process", "light", "dev"}:
        return DevSandboxV2.options(name=actor_name).remote(
            image=spec.image,
            session_id=spec.session_id,
            workspace=str(spec.workspace),
            lsp_actor=spec.lsp_actor,
            protected_paths=spec.protected_paths,
        )

    if driver == "docker":
        from .sandbox_docker import DockerSandboxV2

        opts = dict(spec.driver_options or {})
        network = str(
            opts.get("network") or os.environ.get("BREADBOARD_DOCKER_NETWORK") or "none"
        )
        if network.strip().lower() != "none":
            raise SandboxDriverError(
                "historical Docker sandbox only supports network none",
                code="driver_unsupported",
                driver=driver,
            )
        runtime = opts.get("runtime") or os.environ.get("RAY_DOCKER_RUNTIME")
        docker_bin = (
            opts.get("docker_bin")
            or opts.get("dockerBin")
            or os.environ.get("BREADBOARD_DOCKER_BIN")
        )
        return DockerSandboxV2.options(name=actor_name).remote(
            image=spec.image,
            session_id=spec.session_id,
            workspace=str(spec.workspace),
            lsp_actor=spec.lsp_actor,
            protected_paths=spec.protected_paths,
            network="none",
            runtime=runtime,
            docker_bin=docker_bin,
        )

    raise SandboxDriverError(
        "sandbox driver is unknown or unavailable",
        code="driver_unknown",
        driver=driver,
    )


__all__ = [
    "SandboxDriverError",
    "SandboxLaunchSpec",
    "create_sandbox",
    "resolve_driver_from_env",
]
