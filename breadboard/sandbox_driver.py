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


def resolve_driver_from_env(default: str = "light") -> str:
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

    if driver == "gvisor":
        from .sandbox_gvisor import GVisorSandboxV2

        opts = dict(spec.driver_options or {})
        network = str(opts.get("network") or os.environ.get("BREADBOARD_GVISOR_NETWORK") or "none")
        platform = opts.get("platform") or os.environ.get("GVISOR_PLATFORM") or "systrap"
        docker_bin = opts.get("docker_bin") or os.environ.get("BREADBOARD_DOCKER_BIN")

        return GVisorSandboxV2.options(name=actor_name).remote(
            image=spec.image,
            session_id=spec.session_id,
            workspace=str(spec.workspace),
            lsp_actor=spec.lsp_actor,
            network=network,
            platform=platform,
            docker_bin=docker_bin,
        )

    if driver == "firecracker":
        from .sandbox_firecracker import FirecrackerSandboxV2

        opts = dict(spec.driver_options or {})
        kernel_path = opts.get("kernel_path") or os.environ.get("FIRECRACKER_KERNEL")
        rootfs_path = opts.get("rootfs_path") or os.environ.get("FIRECRACKER_ROOTFS")
        vcpu_count = opts.get("vcpu_count") or os.environ.get("FIRECRACKER_VCPUS")
        mem_size_mib = opts.get("mem_size_mib") or os.environ.get("FIRECRACKER_MEM_MIB")
        network_mode = opts.get("network_mode") or os.environ.get("FIRECRACKER_NETWORK") or "none"
        use_snapshot = opts.get("use_snapshot", False)
        snapshot_path = opts.get("snapshot_path") or os.environ.get("FIRECRACKER_SNAPSHOT")
        snapshot_mem_path = opts.get("snapshot_mem_path") or os.environ.get("FIRECRACKER_SNAPSHOT_MEM")
        snapshot_vsock_path = opts.get("snapshot_vsock_path") or os.environ.get("FIRECRACKER_SNAPSHOT_VSOCK")
        snapshot_resume = opts.get("snapshot_resume")
        enable_diff_snapshots = opts.get("enable_diff_snapshots")
        firecracker_bin = opts.get("firecracker_bin") or os.environ.get("FIRECRACKER_BIN")
        exec_mode = opts.get("exec_mode") or os.environ.get("FIRECRACKER_EXEC_MODE")
        allow_placeholder = opts.get("allow_placeholder")
        vsock_port = opts.get("vsock_port") or os.environ.get("FIRECRACKER_VSOCK_PORT")
        pool_size = opts.get("pool_size") or os.environ.get("FIRECRACKER_POOL_SIZE")
        pool_timeout_s = opts.get("pool_timeout_s") or os.environ.get("FIRECRACKER_POOL_TIMEOUT_S")

        return FirecrackerSandboxV2.options(name=actor_name).remote(
            image=spec.image,
            session_id=spec.session_id,
            workspace=str(spec.workspace),
            lsp_actor=spec.lsp_actor,
            kernel_path=kernel_path,
            rootfs_path=rootfs_path,
            vcpu_count=int(vcpu_count) if vcpu_count else None,
            mem_size_mib=int(mem_size_mib) if mem_size_mib else None,
            network_mode=network_mode,
            use_snapshot=use_snapshot,
            snapshot_path=snapshot_path,
            snapshot_mem_path=snapshot_mem_path,
            snapshot_vsock_path=snapshot_vsock_path,
            snapshot_resume=bool(snapshot_resume) if snapshot_resume is not None else None,
            enable_diff_snapshots=bool(enable_diff_snapshots) if enable_diff_snapshots is not None else None,
            firecracker_bin=firecracker_bin,
            exec_mode=exec_mode,
            allow_placeholder=allow_placeholder,
            vsock_port=int(vsock_port) if vsock_port else None,
            pool_size=int(pool_size) if pool_size else None,
            pool_timeout_s=float(pool_timeout_s) if pool_timeout_s else None,
        )

    if driver == "gvisor":
        from .sandbox_gvisor import GVisorSandboxV2

        opts = dict(spec.driver_options or {})
        network = str(opts.get("network") or os.environ.get("BREADBOARD_GVISOR_NETWORK") or "none")
        platform = opts.get("platform") or os.environ.get("GVISOR_PLATFORM") or "systrap"
        docker_bin = opts.get("docker_bin") or os.environ.get("BREADBOARD_DOCKER_BIN")

        return GVisorSandboxV2.options(name=actor_name).remote(
            image=spec.image,
            session_id=spec.session_id,
            workspace=str(spec.workspace),
            lsp_actor=spec.lsp_actor,
            network=network,
            platform=platform,
            docker_bin=docker_bin,
        )

    if driver == "firecracker":
        from .sandbox_firecracker import FirecrackerSandboxV2

        opts = dict(spec.driver_options or {})
        kernel_path = opts.get("kernel_path") or os.environ.get("FIRECRACKER_KERNEL")
        rootfs_path = opts.get("rootfs_path") or os.environ.get("FIRECRACKER_ROOTFS")
        vcpu_count = opts.get("vcpu_count") or os.environ.get("FIRECRACKER_VCPUS")
        mem_size_mib = opts.get("mem_size_mib") or os.environ.get("FIRECRACKER_MEM_MIB")
        network_mode = opts.get("network_mode") or os.environ.get("FIRECRACKER_NETWORK") or "none"
        use_snapshot = opts.get("use_snapshot", False)
        snapshot_path = opts.get("snapshot_path") or os.environ.get("FIRECRACKER_SNAPSHOT")
        firecracker_bin = opts.get("firecracker_bin") or os.environ.get("FIRECRACKER_BIN")
        exec_mode = opts.get("exec_mode") or os.environ.get("FIRECRACKER_EXEC_MODE")
        allow_placeholder = opts.get("allow_placeholder")

        return FirecrackerSandboxV2.options(name=actor_name).remote(
            image=spec.image,
            session_id=spec.session_id,
            workspace=str(spec.workspace),
            lsp_actor=spec.lsp_actor,
            kernel_path=kernel_path,
            rootfs_path=rootfs_path,
            vcpu_count=int(vcpu_count) if vcpu_count else None,
            mem_size_mib=int(mem_size_mib) if mem_size_mib else None,
            network_mode=network_mode,
            use_snapshot=use_snapshot,
            snapshot_path=snapshot_path,
            firecracker_bin=firecracker_bin,
            exec_mode=exec_mode,
            allow_placeholder=allow_placeholder,
        )

    if driver == "none":
        # Legacy alias: treat as light sandbox for now.
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
