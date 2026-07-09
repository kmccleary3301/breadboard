from __future__ import annotations

import asyncio
import os
import shutil
import tempfile
import threading
import uuid
from pathlib import Path
from typing import Any

import ray

from breadboard.sandbox_driver import SandboxLaunchSpec, create_sandbox

_RAY_INIT_LOCK = threading.Lock()


def _ensure_ray() -> bool:
    if ray.is_initialized():
        return False
    with _RAY_INIT_LOCK:
        if ray.is_initialized():
            return False
        address = os.environ.get("BREADBOARD_HARNESS_RAY_ADDRESS", "").strip() or None
        ray.init(address=address, ignore_reinit_error=True, include_dashboard=False)
        return address is None


async def _ray_get(value: Any) -> Any:
    return await asyncio.to_thread(ray.get, value)


class SandboxCleanupError(RuntimeError):
    pass


class SandboxLease:
    def __init__(
        self,
        *,
        lease_id: str,
        actor: Any,
        workspace: Path,
        driver: str,
        image_digest: str,
        network: str,
    ) -> None:
        self.lease_id = lease_id
        self.actor = actor
        self.workspace = workspace
        self.driver = driver
        self.image_digest = image_digest
        self.network = network
        self._closed = False
        self._cleanup_error: str | None = None
        self._close_lock = asyncio.Lock()

    @property
    def closed(self) -> bool:
        return self._closed

    def attestation(self) -> dict[str, Any]:
        cleanup_state = (
            "failed" if self._cleanup_error else "closed" if self._closed else "active"
        )
        payload = {
            "lease_id": self.lease_id,
            "driver": self.driver,
            "image_digest": self.image_digest,
            "network": self.network,
            "workspace_id": self.workspace.name,
            "isolation_level": "container"
            if self.driver == "docker"
            else "trusted_local_process",
            "cleanup_state": cleanup_state,
        }
        if self._cleanup_error:
            payload["cleanup_error"] = self._cleanup_error
        return payload

    def _assert_open(self) -> None:
        if self._closed or self._cleanup_error:
            raise RuntimeError(f"sandbox lease {self.lease_id!r} is not active")

    async def run_shell(self, command: str, *, timeout: int) -> dict[str, Any]:
        self._assert_open()
        result = await _ray_get(
            self.actor.run_shell.remote(
                command,
                timeout=timeout,
                env={},
                stream=False,
                stdin_data=None,
                shell=True,
            )
        )
        if not isinstance(result, dict):
            raise RuntimeError("sandbox returned a non-mapping command result")
        return dict(result)

    async def read_text(
        self, path: str, *, offset: int = 0, limit: int | None = None
    ) -> dict[str, Any]:
        self._assert_open()
        result = await _ray_get(
            self.actor.read_text.remote(
                path, offset=offset, limit=limit, encoding="utf-8"
            )
        )
        if not isinstance(result, dict):
            raise RuntimeError("sandbox returned a non-mapping read result")
        return dict(result)

    async def write_text(self, path: str, content: str) -> dict[str, Any]:
        self._assert_open()
        result = await _ray_get(
            self.actor.write_text.remote(path, content, encoding="utf-8")
        )
        if not isinstance(result, dict):
            raise RuntimeError("sandbox returned a non-mapping write result")
        return dict(result)

    async def list_files(self, path: str, *, depth: int) -> dict[str, Any]:
        self._assert_open()
        result = await _ray_get(self.actor.ls.remote(path, depth=depth))
        if not isinstance(result, dict):
            raise RuntimeError("sandbox returned a non-mapping list result")
        return dict(result)

    async def stat(self, path: str) -> dict[str, Any]:
        self._assert_open()
        result = await _ray_get(self.actor.stat.remote(path))
        if not isinstance(result, dict):
            raise RuntimeError("sandbox returned a non-mapping stat result")
        return dict(result)

    async def get_bytes(self, path: str) -> bytes:
        self._assert_open()
        result = await _ray_get(self.actor.get.remote(path))
        if not isinstance(result, bytes):
            raise RuntimeError("sandbox returned non-bytes artifact content")
        return result

    async def close(self) -> dict[str, Any]:
        async with self._close_lock:
            if self._closed:
                return self.attestation()
            errors: list[str] = []
            if self.actor is not None:
                try:
                    await asyncio.to_thread(ray.kill, self.actor, no_restart=True)
                    self.actor = None
                except Exception as exc:
                    errors.append(
                        f"actor termination failed: {type(exc).__name__}: {exc}"
                    )
            try:
                await asyncio.to_thread(shutil.rmtree, self.workspace)
            except FileNotFoundError:
                pass
            except Exception as exc:
                errors.append(f"workspace removal failed: {type(exc).__name__}: {exc}")
            if errors:
                self._cleanup_error = "; ".join(errors)
                raise SandboxCleanupError(self._cleanup_error)
            self._cleanup_error = None
            self._closed = True
            return self.attestation()


class SandboxLeaseManager:
    def __init__(self, workspace_root: str | Path | None = None) -> None:
        configured = workspace_root or os.environ.get(
            "BREADBOARD_HARNESS_WORKSPACE_ROOT"
        )
        self.workspace_root = (
            Path(configured).expanduser().resolve() if configured else None
        )
        if self.workspace_root is not None:
            self.workspace_root.mkdir(parents=True, exist_ok=True)
        self._owns_local_ray = False
        self._closed = False
        self._close_lock = asyncio.Lock()

    async def open(
        self,
        *,
        episode_id: str,
        driver: str,
        image_digest: str,
        network: str,
        copy_from: Path | None = None,
    ) -> SandboxLease:
        if self._closed:
            raise RuntimeError("sandbox lease manager is closed")
        self._owns_local_ray = _ensure_ray() or self._owns_local_ray
        prefix = (
            "bb-harness-"
            + "".join(ch if ch.isalnum() else "-" for ch in episode_id)[:32]
            + "-"
        )
        workspace = Path(
            tempfile.mkdtemp(
                prefix=prefix,
                dir=str(self.workspace_root) if self.workspace_root else None,
            )
        )
        lease_id = f"lease-{uuid.uuid4().hex}"
        actor_name = f"bb-harness-{uuid.uuid4().hex}"
        try:
            if copy_from is not None:
                await asyncio.to_thread(
                    shutil.copytree, copy_from, workspace, dirs_exist_ok=True
                )
            actor = create_sandbox(
                SandboxLaunchSpec(
                    driver=driver,
                    image=image_digest,
                    workspace=str(workspace),
                    session_id=lease_id,
                    name=actor_name,
                    driver_options={"network": network},
                )
            )
        except BaseException as exc:
            try:
                await asyncio.to_thread(shutil.rmtree, workspace)
            except FileNotFoundError:
                pass
            except Exception as cleanup_exc:
                raise SandboxCleanupError(
                    f"failed to remove workspace after sandbox creation error: {cleanup_exc}"
                ) from exc
            raise
        return SandboxLease(
            lease_id=lease_id,
            actor=actor,
            workspace=workspace,
            driver=driver,
            image_digest=image_digest,
            network=network,
        )

    async def close(self) -> None:
        async with self._close_lock:
            if self._closed:
                return
            self._closed = True
            if self._owns_local_ray and ray.is_initialized():
                ray.shutdown()
            self._owns_local_ray = False


__all__ = ["SandboxCleanupError", "SandboxLease", "SandboxLeaseManager"]
